"""Unlimited context & media synchronization to Google Drive via rclone."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Any

from tg_bot.config import (
    DRIVE_ROOT,
    DRIVE_SYNC_STATE_FILE,
    RCLONE_BIN,
    WORK_DIR,
)

LOGGER = logging.getLogger("tg_bot.drive")
CONTEXT_SYNC_LOCK = threading.Lock()


def chat_scope_for_task(task: dict[str, Any]) -> str:
    """Return canonical Drive scope folder name for a group or private chat."""
    chat_id = int(task["chat_id"])
    if chat_id > 0:
        sender_id = task.get("sender_id") or chat_id
        return f"private-{sender_id}"
    return f"group-{abs(chat_id)}"


def chat_scope_for_id(chat_id: int) -> str:
    return f"private-{chat_id}" if chat_id > 0 else f"group-{abs(chat_id)}"


def _load_sync_hashes() -> dict[str, str]:
    if not DRIVE_SYNC_STATE_FILE.is_file():
        return {}
    try:
        data = json.loads(DRIVE_SYNC_STATE_FILE.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_sync_hashes(hashes: dict[str, str]) -> None:
    DRIVE_SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DRIVE_SYNC_STATE_FILE.write_text(
        json.dumps(hashes, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _run_rclone(args: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(RCLONE_BIN), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        raise RuntimeError(f"rclone failed ({result.returncode}): {stderr or stdout}")
    return result


def archive_on_drive(
    path: Path,
    task: dict[str, Any],
    *,
    category: str,
    stable_key: str,
) -> str:
    """Upload a media artifact (incoming, voice_out, generated, sticker, montage) to Drive."""
    scope = chat_scope_for_task(task)
    month = datetime.now(UTC).strftime("%Y-%m")
    digest = hashlib.sha256(stable_key.encode()).hexdigest()[:16]
    suffix = path.suffix.lower() or ".bin"
    destination = f"{DRIVE_ROOT}/{scope}/{month}/{category}/{digest}{suffix}"
    _run_rclone(
        ["copyto", str(path), destination, "--drive-chunk-size", "64M", "--retries", "3"],
        timeout=900,
    )
    return destination


def write_local_full_context(
    task: dict[str, Any], work_dir: Path, context_attachments: tuple[Path, ...]
) -> Path:
    """Write complete unlimited chat history to work_dir/full-context.jsonl."""
    media_iter = iter(context_attachments)
    rows: list[dict[str, Any]] = []
    for item in task.get("context", []):
        row: dict[str, Any] = {
            "message_id": item.get("message_id"),
            "source": item.get("source", "диалог"),
            "author": item.get("author", ""),
            "text": item.get("text", ""),
            "created_at": item.get("created_at"),
        }
        if item.get("reply_to_message_id"):
            row["reply_to_message_id"] = item["reply_to_message_id"]
        if item.get("decision_flag"):
            row["decision_flag"] = item["decision_flag"]
        if item.get("photo_file_id"):
            attachment = next(media_iter, None)
            row["media_file"] = attachment.name if attachment else None
        rows.append(row)
    snapshot = work_dir / "full-context.jsonl"
    snapshot.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return snapshot


def sync_contexts_payload(
    contexts: list[dict[str, Any]], *, force: bool = False
) -> dict[str, str]:
    """Upload changed unlimited history.jsonl snapshots for every chat to Google Drive."""
    WORK_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    known_hashes = _load_sync_hashes()
    synced: dict[str, str] = {}

    with tempfile.TemporaryDirectory(prefix="context-sync-", dir=WORK_DIR) as directory:
        root = Path(directory)
        for context in contexts:
            chat_id = int(context["chat_id"])
            scope = chat_scope_for_id(chat_id)
            rows = context.get("rows", [])
            content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if not force and known_hashes.get(scope) == digest:
                continue

            source = root / f"{scope}.jsonl"
            source.write_text(content, encoding="utf-8")
            destination = f"{DRIVE_ROOT}/{scope}/context/history.jsonl"
            _run_rclone(["copyto", str(source), destination, "--retries", "3"], timeout=300)
            known_hashes[scope] = digest
            synced[scope] = f"{len(rows)} rows"
            LOGGER.info("Synced unlimited context to Drive: %s (%d rows)", destination, len(rows))

    if synced:
        _save_sync_hashes(known_hashes)
    return synced


def sync_all_text_contexts_in_background(fetch_contexts_fn, *, force: bool = False) -> None:
    """Run Drive context sync in a single background thread without blocking queue polling."""
    if not CONTEXT_SYNC_LOCK.acquire(blocking=False):
        return
    try:
        contexts = fetch_contexts_fn()
        sync_contexts_payload(contexts, force=force)
    except Exception:
        LOGGER.exception("Full text context sync to Google Drive failed")
    finally:
        CONTEXT_SYNC_LOCK.release()
