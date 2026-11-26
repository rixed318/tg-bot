"""Central configuration paths and runtime constants for tg-bot."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

AGY_BIN = Path(os.environ.get("TG_BOT_AGY_BIN", "/home/rus/.local/bin/agy-proxy"))
TRIGGER_MODEL = os.environ.get("TG_BOT_TRIGGER_MODEL", "gemini-4-hydrogen-medium")
REPLY_MODEL = os.environ.get("TG_BOT_REPLY_MODEL", "gemini-4-hydrogen-high")
WHISPER_MODEL = os.environ.get("TG_BOT_WHISPER_MODEL", "large-v3-turbo")
RCLONE_BIN = Path(os.environ.get("TG_BOT_RCLONE_BIN", "/home/rus/.local/bin/rclone"))
FFMPEG_BIN = os.environ.get("TG_BOT_FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.environ.get("TG_BOT_FFPROBE_BIN", "ffprobe")

ENV_FILE = Path(
    os.environ.get("TG_BOT_ENV_FILE", str(Path.home() / ".config" / "rixed-joke-bot" / "env"))
)
REMOTE_SSH_HOST = os.environ.get("TG_BOT_REMOTE_HOST", "rixed")
REMOTE_PYTHON = os.environ.get(
    "TG_BOT_REMOTE_PYTHON", "/opt/rixed/telegram-courier/.venv/bin/python"
)
REMOTE_QUEUE_SCRIPT = os.environ.get(
    "TG_BOT_REMOTE_QUEUE", "/opt/rixed-joke-bot/joke_bot_queue.py"
)

WORK_DIR = Path(os.environ.get("TG_BOT_WORK_DIR", "/tmp/rixed-joke-gemini"))
CACHE_DIR = Path(
    os.environ.get("TG_BOT_CACHE_DIR", str(Path.home() / ".cache" / "rixed-joke-bot"))
)
MEDIA_CACHE_ROOT = CACHE_DIR / "media"
DRIVE_SYNC_STATE_FILE = CACHE_DIR / "drive_sync_state.json"
DRIVE_ROOT = os.environ.get("TG_BOT_DRIVE_ROOT", "gdrive:Тау бао — медиа")

VOICES_DIR = Path(os.environ.get("TG_BOT_VOICES_DIR", str(REPO_ROOT / "voices")))
VOICE_PROFILES_FILE = VOICES_DIR / "profiles.json"

# External optional media/montage scripts (legacy rixed-music integrations)
RIXED_MUSIC_VENV_PY = Path("/home/rus/dev/rixed-music/.venv/bin/python")
CUTOUT_SCRIPT = Path("/home/rus/dev/rixed-music/scripts/telegram_bot_cutout.py")
STICKER_GIF_SCRIPT = Path("/home/rus/dev/rixed-music/scripts/telegram_bot_sticker_gif.py")
MONTAGE_SCRIPT = Path("/home/rus/dev/rixed-music/scripts/telegram_bot_montage.py")
ANYA_MATERIALS_DIR = Path("/home/rus/dev/rixed-music/data/montage-materials/downloaded")
YT_DLP_BIN = Path(
    os.environ.get("TG_BOT_YTDLP_BIN", "/home/rus/dev/rixed-music/.venv/bin/yt-dlp")
)

# Trigger & batching tuning
GROUP_DEBOUNCE_SECONDS = float(os.environ.get("TG_BOT_DEBOUNCE_SECONDS", "4.0"))
GROUP_COOLDOWN_SECONDS = float(os.environ.get("TG_BOT_COOLDOWN_SECONDS", "45.0"))
MAX_UNANSWERED_WINDOW = int(os.environ.get("TG_BOT_MAX_UNANSWERED", "8"))
UNANSWERED_LOOKBACK_MINUTES = int(os.environ.get("TG_BOT_UNANSWERED_MINUTES", "45"))
INLINE_HISTORY_LIMIT = int(os.environ.get("TG_BOT_INLINE_HISTORY", "25"))
DRIVE_SYNC_INTERVAL_SECONDS = int(os.environ.get("TG_BOT_DRIVE_SYNC_INTERVAL", "120"))

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_MEDIA_BYTES = 20 * 1024 * 1024

PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}
AUDIO_SUFFIXES = {".oga", ".ogg", ".mp3", ".wav", ".m4a", ".aac"}
MEDIA_SUFFIXES = PHOTO_SUFFIXES | VIDEO_SUFFIXES | AUDIO_SUFFIXES


def read_bot_env() -> dict[str, str]:
    """Read key=value settings from ENV_FILE without leaking secrets."""
    values: dict[str, str] = {}
    if ENV_FILE.is_file():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def read_token() -> str:
    """Return BOT_TOKEN from environment or ENV_FILE."""
    env_token = os.environ.get("BOT_TOKEN", "").strip()
    if env_token:
        return env_token
    token = read_bot_env().get("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(f"BOT_TOKEN is missing in env and {ENV_FILE}")
    return token


def read_bot_username() -> str:
    """Return BOT_USERNAME from environment or ENV_FILE (without leading @)."""
    raw = os.environ.get("BOT_USERNAME", "").strip() or read_bot_env().get("BOT_USERNAME", "")
    return raw.lstrip("@").strip()
