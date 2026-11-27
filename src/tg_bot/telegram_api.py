"""Telegram Bot HTTP API helpers (voice-first, zero text clutter)."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
import secrets
import time
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
    metadata_url = (
        f"https://api.telegram.org/bot{token}/getFile?"
        + urllib.parse.urlencode({"file_id": file_id})
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(metadata_url, timeout=30) as response:
                file_path = str(json.load(response)["result"]["file_path"])
            break
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)
    else:
        assert last_error is not None
        raise last_error
    suffix = Path(file_path).suffix.lower()
    if suffix not in MEDIA_SUFFIXES:
        suffix = ".jpg"
    target = work_dir / f"{stem}{suffix}"
    limit = MAX_MEDIA_BYTES if suffix in (VIDEO_SUFFIXES | AUDIO_SUFFIXES) else MAX_PHOTO_BYTES
    media_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(media_url, timeout=120) as response:
                data = response.read(limit + 1)
            break
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)
    else:
        assert last_error is not None
        raise last_error
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


def send_rich_html(
    token: str,
    task: dict[str, Any],
    html_text: str,
    reply_to_message_id: int | None = None,
) -> int:
    """Send a Telegram Rich HTML message via Bot API sendMessage."""
    params: dict[str, Any] = {
        "chat_id": task["chat_id"],
        "text": html_text[:4096],
        "parse_mode": "HTML",
    }
    if reply_to_message_id is not None:
        params["reply_parameters"] = json.dumps(
            {"message_id": reply_to_message_id, "allow_sending_without_reply": True}
        )
    if task.get("thread_id"):
        params["message_thread_id"] = task["thread_id"]
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=urllib.parse.urlencode(params).encode(),
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode())
    if not payload.get("ok"):
        raise RuntimeError(str(payload))
    return int(payload["result"]["message_id"])


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


_BOT_USERNAME_CACHE: dict[str, str] = {}


def get_bot_username(token: str) -> str:
    if token in _BOT_USERNAME_CACHE:
        return _BOT_USERNAME_CACHE[token]
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram getMe failed: {payload}")
    uname = str(payload["result"]["username"]).strip()
    _BOT_USERNAME_CACHE[token] = uname
    return uname


def create_sticker_set(
    token: str,
    *,
    user_id: int,
    title: str,
    sticker_paths: list[Path],
    is_emoji: bool = False,
    emoji: str = "✨",
) -> str:
    """Create a new Telegram sticker or custom emoji set and return its t.me/addstickers/ URL."""
    if not sticker_paths:
        raise ValueError("No stickers provided for sticker set")
    bot_uname = get_bot_username(token)
    suffix = f"_by_{bot_uname}"
    stem = f"pack_{user_id}_{int(time.time()) % 1000000}"
    name = f"{stem[: 64 - len(suffix)]}{suffix}"

    boundary = f"----TgBotPack{secrets.token_hex(12)}"
    body = bytearray()

    def _add_field(k: str, v: str) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))

    stickers_spec = []
    batch = sticker_paths[:50]
    for idx, path in enumerate(batch):
        fmt = "video" if path.suffix.lower() == ".webm" else "static"
        stickers_spec.append(
            {"sticker": f"attach://s{idx}", "format": fmt, "emoji_list": [emoji]}
        )

    _add_field("user_id", str(user_id))
    _add_field("name", name)
    _add_field("title", title[:64] or "Sticker Pack")
    _add_field("stickers", json.dumps(stickers_spec, ensure_ascii=False))
    if is_emoji:
        _add_field("sticker_type", "custom_emoji")

    for idx, path in enumerate(batch):
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="s{idx}"; filename="{path.name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode("utf-8")
        )
        body.extend(path.read_bytes())
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/createNewStickerSet",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"createNewStickerSet failed: {payload}")
    return f"https://t.me/addstickers/{name}"
