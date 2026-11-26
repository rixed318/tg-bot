"""Telegram Bot HTTP API helpers (voice-first, zero text clutter)."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
import secrets
from typing import Any
import urllib.parse
import urllib.request

from tg_bot.config import (
    MAX_MEDIA_BYTES,
    MAX_PHOTO_BYTES,
    AUDIO_SUFFIXES,
    MEDIA_SUFFIXES,
    VIDEO_SUFFIXES,
)
from tg_bot.delete_messages import record_bot_message


def multipart_form_data(
    fields: dict[str, str], file_field: str, path: Path
) -> tuple[bytes, str]:
    """Encode multipart/form-data payload for Telegram media uploads."""
    boundary = f"tgbot-{secrets.token_hex(16)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{path.name}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), boundary


def download_telegram_file(
    token: str, file_id: str, work_dir: Path, stem: str = "input"
) -> Path:
    """Download an incoming Telegram attachment up to configured size limits."""
    with urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/getFile?"
        + urllib.parse.urlencode({"file_id": file_id}),
        timeout=20,
    ) as response:
        file_path = str(json.load(response)["result"]["file_path"])
    suffix = Path(file_path).suffix.lower()
    if suffix not in MEDIA_SUFFIXES:
        suffix = ".jpg"
    target = work_dir / f"{stem}{suffix}"
    limit = MAX_MEDIA_BYTES if suffix in (VIDEO_SUFFIXES | AUDIO_SUFFIXES) else MAX_PHOTO_BYTES
    with urllib.request.urlopen(
        f"https://api.telegram.org/file/bot{token}/{file_path}", timeout=60
    ) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Telegram media exceeds {limit // (1024 * 1024)} MB")
    target.write_bytes(data)
    return target


def send_chat_action(token: str, task: dict[str, Any], action: str = "record_voice") -> None:
    """Send Telegram chat action (default: record_voice)."""
    params: dict[str, Any] = {"chat_id": task["chat_id"], "action": action}
    if task.get("thread_id"):
        params["message_thread_id"] = task["thread_id"]
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendChatAction",
        data=urllib.parse.urlencode(params).encode(),
    )
    try:
        with urllib.request.urlopen(request, timeout=15):
            pass
    except Exception:
        pass


def _build_reply_fields(
    task: dict[str, Any], reply_to_message_id: int | None
) -> dict[str, str]:
    fields: dict[str, str] = {"chat_id": str(task["chat_id"])}
    if reply_to_message_id is not None:
        fields["reply_parameters"] = json.dumps(
            {"message_id": reply_to_message_id, "allow_sending_without_reply": True}
        )
    if task.get("thread_id"):
        fields["message_thread_id"] = str(task["thread_id"])
    return fields


def _send_multipart(
    token: str,
    method: str,
    file_field: str,
    path: Path,
    task: dict[str, Any],
    reply_to_message_id: int | None,
    *,
    extra_fields: dict[str, str] | None = None,
    timeout: int = 120,
) -> int:
    fields = _build_reply_fields(task, reply_to_message_id)
    if extra_fields:
        fields.update(extra_fields)
    body, boundary = multipart_form_data(fields, file_field, path)
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        mid = int(json.load(response)["result"]["message_id"])
        record_bot_message(int(task["chat_id"]), mid, int(task.get("id") or 0))
        return mid


def send_voice(
    token: str,
    task: dict[str, Any],
    ogg_path: Path,
    *,
    duration_seconds: int | None = None,
    reply_to_message_id: int | None = None,
) -> int:
    """Send an OGG/Opus Telegram voice message without text captions."""
    extra: dict[str, str] = {}
    if duration_seconds is not None and duration_seconds > 0:
        extra["duration"] = str(duration_seconds)
    return _send_multipart(
        token,
        "sendVoice",
        "voice",
        ogg_path,
        task,
        reply_to_message_id,
        extra_fields=extra,
        timeout=90,
    )


def send_video_note(
    token: str,
    task: dict[str, Any],
    mp4_path: Path,
    *,
    duration_seconds: int | None = None,
    length: int = 384,
    reply_to_message_id: int | None = None,
) -> int:
    """Send a square MP4 as a circular Telegram video note."""
    extra: dict[str, str] = {"length": str(length)}
    if duration_seconds is not None and duration_seconds > 0:
        extra["duration"] = str(duration_seconds)
    return _send_multipart(
        token,
        "sendVideoNote",
        "video_note",
        mp4_path,
        task,
        reply_to_message_id,
        extra_fields=extra,
        timeout=120,
    )


def send_photo(
    token: str, task: dict[str, Any], path: Path, reply_to_message_id: int | None
) -> int:
    return _send_multipart(token, "sendPhoto", "photo", path, task, reply_to_message_id, timeout=60)


def send_video(
    token: str, task: dict[str, Any], path: Path, reply_to_message_id: int | None
) -> int:
    return _send_multipart(
        token,
        "sendVideo",
        "video",
        path,
        task,
        reply_to_message_id,
        extra_fields={"supports_streaming": "true"},
        timeout=300,
    )


def send_document(
    token: str, task: dict[str, Any], path: Path, reply_to_message_id: int | None
) -> int:
    return _send_multipart(
        token, "sendDocument", "document", path, task, reply_to_message_id, timeout=900
    )


def send_sticker(
    token: str, task: dict[str, Any], path: Path, reply_to_message_id: int | None
) -> int:
    return _send_multipart(
        token, "sendSticker", "sticker", path, task, reply_to_message_id, timeout=60
    )


def send_animation(
    token: str, task: dict[str, Any], path: Path, reply_to_message_id: int | None
) -> int:
    return _send_multipart(
        token, "sendAnimation", "animation", path, task, reply_to_message_id, timeout=120
    )


def copy_current_message_to_group(token: str, task: dict[str, Any]) -> int:
    """Copy a private-chat media message directly into the configured group."""
    target = int(task.get("configured_group_id") or 0)
    source = int(task["chat_id"])
    if target >= 0:
        raise RuntimeError("Configured target group is missing")
    if source == target:
        raise ValueError("Current message is already in the configured group")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/copyMessage",
        data=urllib.parse.urlencode(
            {
                "chat_id": target,
                "from_chat_id": source,
                "message_id": task["message_id"],
            }
        ).encode(),
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        mid = int(json.load(response)["result"]["message_id"])
        record_bot_message(target, mid, int(task.get("id") or 0))
        return mid
