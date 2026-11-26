"""Directive parsing and local media tool runners (image gen, cutout, stickers, montage, yt-dlp)."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import logging
from pathlib import Path
import random
import re
import shutil
import socket
import subprocess
from typing import Any
from urllib.parse import urlsplit

from tg_bot.config import (
    AGY_BIN,
    ANYA_MATERIALS_DIR,
    CUTOUT_SCRIPT,
    DRIVE_ROOT,
    MAX_PHOTO_BYTES,
    MEDIA_CACHE_ROOT,
    MONTAGE_SCRIPT,
    PHOTO_SUFFIXES,
    RCLONE_BIN,
    REPLY_MODEL,
    RIXED_MUSIC_VENV_PY,
    STICKER_GIF_SCRIPT,
    VIDEO_SUFFIXES,
    WORK_DIR,
    YT_DLP_BIN,
)

LOGGER = logging.getLogger("tg_bot.media")

PHOTO_DIRECTIVE = re.compile(r"^\[\[telegram-photo:(?P<path>[^\]]+)\]\]$")
IMAGE_PROMPT_DIRECTIVE = re.compile(r"^\[\[image-prompt:(?P<prompt>.+)\]\]$")
DOWNLOAD_DIRECTIVE = re.compile(r"^\[\[download-url:(?P<url>https?://[^\]]+)\]\]$")
DOWNLOAD_TO_GROUP_DIRECTIVE = re.compile(
    r"^\[\[download-url-to-group:(?P<url>https?://[^\]]+)\]\]$"
)
COPY_TO_GROUP_DIRECTIVE = re.compile(r"^\[\[copy-current-message-to-group\]\]$")
SEND_TO_GROUP_DIRECTIVE = re.compile(r"^\[\[send-to-group\]\]$")
CUTOUT_DIRECTIVE = re.compile(r"^\[\[cutout-photo\]\]$")
MONTAGE_DIRECTIVE = re.compile(r"^\[\[montage-video(?::(?P<params>.*))?\]\]$")
DELETE_PREVIOUS_DIRECTIVE = re.compile(r"^\[\[delete-previous-message\]\]$")
DELETE_ALL_DIRECTIVE = re.compile(r"^\[\[delete-(?:all-)?bot-messages(?::(?P<count>\d+|all))?\]\]$")
STICKER_DIRECTIVE = re.compile(r"^\[\[(?:make-)?sticker\]\]$")
GIF_DIRECTIVE = re.compile(r"^\[\[(?:make-)?gif\]\]$")
EMOTION_DIRECTIVE = re.compile(r"^\[\[emotion:(?P<emotion>[a-zA-Z_-]+)\]\]$")

MEDIA_CATALOG: dict[str, dict[str, Any]] = {
    "anya_tiktok": {
        "keywords": ["аня", "ане", "ани", "анну", "annimoor", "любв", "признани", "романтик"],
        "default_style": "romantic",
        "default_hook": "ДЛЯ ТЕБЯ ❤️",
        "local_paths": [
            Path("/home/rus/.cache/rixed-joke-bot/media/group-1003800116774/8f0c7751fb5252aa.mp4"),
            Path("/home/rus/Монтаж/Девушки/1.mp4"),
        ],
        "remote_path": "group-1003800116774/2026-10/downloads/8f0c7751fb5252aa.mp4",
    },
    "girls_library": {
        "keywords": ["альтушк", "девушк", "тян", "вайб", "красотк"],
        "default_style": "romantic",
        "default_hook": "ВАЙБ ДНЯ ✨",
        "local_paths": [
            Path("/home/rus/Монтаж/Девушки/Альтушки/video_2025-01-08_21-51-15.mp4"),
            Path("/home/rus/Монтаж/Девушки/1.mp4"),
        ],
        "remote_path": None,
    },
    "phonk_action": {
        "keywords": ["фонк", "экшн", "драк", "боев", "бас", "дрифт", "блендер", "сигм", "кринж", "тау"],
        "default_style": "phonk",
        "default_hook": "BLENDER MOMENT 🔥",
        "local_paths": [
            Path("/home/rus/Монтаж/Фоны/Ретро неон 1.mp4"),
            Path("/home/rus/Монтаж/Фоны/Ретро неон 2.mp4"),
        ],
        "remote_path": None,
    },
}


@dataclass(frozen=True)
class AgentResponse:
    text: str
    photos: tuple[Path, ...]
    image_prompt: str | None = None
    download_url: str | None = None
    download_to_group: bool = False
    copy_current_to_group: bool = False
    cutout_photo: bool = False
    montage_video: bool = False
    montage_target: str | None = None
    montage_style: str | None = None
    montage_hook: str | None = None
    delete_previous: bool = False
    delete_all_bot_messages: bool = False
    make_sticker: bool = False
    make_gif: bool = False
    emotion: str = "neutral"


def parse_agent_response(response: str, work_dir: Path) -> AgentResponse:
    """Separate spoken reply text from internal action/media directives."""
    outbox = (work_dir / "outbox").resolve()
    text_lines: list[str] = []
    photos: list[Path] = []
    image_prompt: str | None = None
    download_url: str | None = None
    download_to_group = False
    copy_current_to_group = False
    cutout_photo = False
    montage_video = False
    montage_target: str | None = None
    montage_style: str | None = None
    montage_hook: str | None = None
    delete_previous = False
    delete_all_bot_messages = False
    make_sticker = False
    make_gif = False
    emotion = "neutral"

    for line in response.splitlines():
        stripped = line.strip()
        emotion_match = EMOTION_DIRECTIVE.fullmatch(stripped)
        if emotion_match:
            emotion = emotion_match.group("emotion").strip().lower()
            continue
        if DELETE_ALL_DIRECTIVE.fullmatch(stripped):
            delete_all_bot_messages = True
            continue
        if DELETE_PREVIOUS_DIRECTIVE.fullmatch(stripped):
            delete_previous = True
            continue
        if STICKER_DIRECTIVE.fullmatch(stripped):
            make_sticker = True
            cutout_photo = True
            continue
        if GIF_DIRECTIVE.fullmatch(stripped):
            make_gif = True
            continue
        if COPY_TO_GROUP_DIRECTIVE.fullmatch(stripped):
            copy_current_to_group = True
            continue
        if SEND_TO_GROUP_DIRECTIVE.fullmatch(stripped):
            download_to_group = True
            continue
        if CUTOUT_DIRECTIVE.fullmatch(stripped):
            cutout_photo = True
            continue
        montage_match = MONTAGE_DIRECTIVE.fullmatch(stripped)
        if montage_match:
            montage_video = True
            raw_params = (montage_match.group("params") or "").strip()
            if "=" in raw_params:
                for part in (p.strip() for p in raw_params.split("|")):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        k, v = k.strip().lower(), v.strip()
                        if k == "target":
                            montage_target = v[:100]
                        elif k == "style":
                            montage_style = v[:50]
                        elif k == "hook":
                            montage_hook = v[:200]
            elif raw_params:
                montage_hook = raw_params[:200]
            continue
        group_download_match = DOWNLOAD_TO_GROUP_DIRECTIVE.fullmatch(stripped)
        if group_download_match:
            download_url = group_download_match.group("url").strip()[:2048]
            download_to_group = True
            continue
        download_match = DOWNLOAD_DIRECTIVE.fullmatch(stripped)
        if download_match:
            download_url = download_match.group("url").strip()[:2048]
            continue
        image_match = IMAGE_PROMPT_DIRECTIVE.fullmatch(stripped)
        if image_match:
            candidate = image_match.group("prompt").strip()
            if candidate:
                image_prompt = candidate[:1200]
            continue
        photo_match = PHOTO_DIRECTIVE.fullmatch(stripped)
        if not photo_match:
            text_lines.append(line)
            continue
        path = (work_dir / photo_match.group("path")).resolve()
        if not path.is_relative_to(outbox):
            raise ValueError("Outgoing image must be inside outbox")
        if path.suffix.lower() not in PHOTO_SUFFIXES or not path.is_file():
            raise ValueError("Outgoing image is missing or unsupported")
        if path.stat().st_size > MAX_PHOTO_BYTES:
            raise ValueError("Outgoing image exceeds 10 MB")
        photos.append(path)

    text = "\n".join(text_lines).strip()
    if photos and not text:
        text = "Лови картинку, сделал как просили."
    if delete_all_bot_messages and not text:
        text = "Вычистил все свои сообщения за собой."
    if (make_sticker or make_gif) and not text:
        text = "Лови готовый стикер." if make_sticker else "Лови гифку."
    if (
        image_prompt
        or download_url
        or copy_current_to_group
        or cutout_photo
        or montage_video
        or delete_previous
    ) and not text:
        text = "Принял, всё готово."

    return AgentResponse(
        text=text[:3900],
        photos=tuple(photos),
        image_prompt=image_prompt,
        download_url=download_url,
        download_to_group=download_to_group,
        copy_current_to_group=copy_current_to_group,
        cutout_photo=cutout_photo,
        montage_video=montage_video,
        montage_target=montage_target,
        montage_style=montage_style,
        montage_hook=montage_hook,
        delete_previous=delete_previous,
        delete_all_bot_messages=delete_all_bot_messages,
        make_sticker=make_sticker,
        make_gif=make_gif,
        emotion=emotion,
    )


def validate_download_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Нужна публичная HTTP(S)-ссылка")
    if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise ValueError("Ссылка содержит запрещённые параметры доступа")
    for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443):
        address = ipaddress.ip_address(item[4][0].split("%", 1)[0])
        if not address.is_global:
            raise ValueError("Локальные и служебные адреса запрещены")


def download_public_media(url: str, work_dir: Path) -> tuple[Path, ...]:
    validate_download_url(url)
    target = work_dir / "downloads"
    target.mkdir(mode=0o700, exist_ok=True)
    yt_dlp = str(YT_DLP_BIN if YT_DLP_BIN.is_file() else "yt-dlp")
    command = [
        yt_dlp,
        "--playlist-items",
        "1",
        "--restrict-filenames",
        "--max-filesize",
        "4G",
        "--paths",
        str(target),
        "--print",
        "after_move:filepath",
        url,
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=1200)
    paths = tuple(
        Path(line.strip()).resolve()
        for line in result.stdout.splitlines()
        if line.strip() and Path(line.strip()).is_file()
    )
    if not paths or any(not path.is_relative_to(target.resolve()) for path in paths):
        raise RuntimeError("Загрузчик не вернул безопасный файл")
    return paths


def generate_image(image_prompt: str, work_dir: Path) -> Path:
    outbox = work_dir / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    output = outbox / "generated.png"
    brief = re.sub(r"[\x00-\x1f\x7f]", " ", image_prompt).strip()
    prompt_text = (
        "Use the image-generator subagent to generate an image based on this description: "
        f"{brief}\n"
        "Save or copy the resulting image to outbox/generated.png in the current working directory. Do not do anything else."
    )
    subprocess.run(
        [
            str(AGY_BIN),
            "--model",
            REPLY_MODEL,
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
            "--print-timeout",
            "180s",
            f"--print={prompt_text}",
        ],
        cwd=work_dir,
        check=True,
        capture_output=True,
        text=True,
        timeout=240,
    )
    if not output.is_file():
        candidates = sorted(outbox.glob("*.png")) + sorted(outbox.glob("*.jpg"))
        if candidates:
            return candidates[0]
        raise RuntimeError("Gemini не создал файл outbox/generated.png")
    if output.stat().st_size > MAX_PHOTO_BYTES:
        raise ValueError("Generated image exceeds 10 MB")
    return output


def save_chat_video(task: dict[str, Any], path: Path) -> None:
    scope = (
        f"private-{task['sender_id']}"
        if int(task["chat_id"]) > 0
        else f"group-{abs(int(task['chat_id']))}"
    )
    chat_cache = MEDIA_CACHE_ROOT / scope
    chat_cache.mkdir(parents=True, exist_ok=True)
    target = chat_cache / path.name
    if path.resolve() != target.resolve():
        try:
            shutil.copy2(path, target)
        except Exception:
            pass


def find_source_video(
    task: dict[str, Any], target: str | None, work_dir: Path
) -> tuple[Path | None, str, str]:
    target_key = (target or "").lower().strip()
    user_text = str(task.get("text") or "").lower()

    is_anya = (target_key == "anya_tiktok") or any(
        kw in user_text for kw in MEDIA_CATALOG["anya_tiktok"]["keywords"]
    )
    if is_anya and ANYA_MATERIALS_DIR.is_dir():
        clips = sorted(p for p in ANYA_MATERIALS_DIR.glob("*.mp4") if p.stat().st_size > 150_000)
        if clips:
            return random.choice(clips), "romantic", "ДЛЯ ТЕБЯ ❤️"

    for cat_id, entry in MEDIA_CATALOG.items():
        if target_key == cat_id or any(kw in user_text for kw in entry.get("keywords", [])):
            for p in entry.get("local_paths", []):
                if p.is_file():
                    return p, entry.get("default_style", "auto"), entry.get("default_hook", "")
            remote = entry.get("remote_path")
            if remote:
                target_local = work_dir / Path(remote).name
                try:
                    subprocess.run(
                        [str(RCLONE_BIN), "copyto", f"{DRIVE_ROOT}/{remote}", str(target_local)],
                        check=True,
                        timeout=45,
                    )
                    if target_local.is_file():
                        return (
                            target_local,
                            entry.get("default_style", "auto"),
                            entry.get("default_hook", ""),
                        )
                except Exception:
                    pass

    scopes: list[str] = []
    if int(task["chat_id"]) > 0:
        scopes.append(f"private-{task['sender_id']}")
        if task.get("configured_group_id"):
            scopes.append(f"group-{abs(int(task['configured_group_id']))}")
    else:
        scopes.append(f"group-{abs(int(task['chat_id']))}")
        if task.get("sender_id"):
            scopes.append(f"private-{task['sender_id']}")

    for scope in scopes:
        chat_cache = MEDIA_CACHE_ROOT / scope
        if chat_cache.is_dir():
            local_videos = sorted(
                (p for p in chat_cache.glob("*") if p.suffix.lower() in VIDEO_SUFFIXES),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if local_videos:
                return local_videos[0], "auto", "ТАУ БАО ЭДИТ 🔥"
    return None, "", ""


def run_sticker_or_gif(source_media: Path, output_path: Path, mode: str) -> Path:
    py = str(RIXED_MUSIC_VENV_PY if RIXED_MUSIC_VENV_PY.is_file() else "python3")
    script = str(STICKER_GIF_SCRIPT)
    subprocess.run(
        [py, script, str(source_media), str(output_path), "--mode", mode],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return output_path


def run_montage(source_video: Path, output_path: Path, hook: str, style: str) -> Path:
    py = str(RIXED_MUSIC_VENV_PY if RIXED_MUSIC_VENV_PY.is_file() else "python3")
    subprocess.run(
        [py, str(MONTAGE_SCRIPT), str(source_video), str(output_path), hook, "--style", style],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return output_path


def cleanup_cutout_helper_available() -> bool:
    return CUTOUT_SCRIPT.is_file() and RIXED_MUSIC_VENV_PY.is_file()
