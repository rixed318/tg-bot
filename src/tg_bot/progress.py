"""Rate-limit-safe real-time Telegram progress bar for photo/video/pack generation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import threading
import time
from typing import Any
import urllib.error
import urllib.request

from tg_bot.config import PHOTO_SUFFIXES, VIDEO_SUFFIXES

LOGGER = logging.getLogger(__name__)

_PHOTO_INTENT_RE = re.compile(
    r"(?:\bфото\w*|\bкартинк\w*|\bизображен\w*|\bарт\b|\bпортрет\w*|сгенерируй\s+.*ксиюш|сгенерируй\s+.*секси)",
    re.IGNORECASE,
)
_VIDEO_INTENT_RE = re.compile(
    r"(?:\bвидео\w*|\bролик\w*|\bклип\w*|\bанимац\w*|\bмонтаж\w*)",
    re.IGNORECASE,
)
_PACK_INTENT_RE = re.compile(
    r"(?:\bстикер\w*|\bэмодж\w*|\bэмодз\w*|\bпак\w*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MediaIntent:
    wants_photo: bool
    wants_video: bool
    wants_pack: bool

    @property
    def enabled(self) -> bool:
        return self.wants_photo or self.wants_video or self.wants_pack


def detect_media_intent(text: str) -> MediaIntent:
    """Detect whether the user request asks for photo, video, or sticker/emoji pack."""
    src = text or ""
    wants_pack = bool(_PACK_INTENT_RE.search(src))
    wants_video = bool(_VIDEO_INTENT_RE.search(src))
    # "из этих изображений" / "из этих фото" in a pack request is input material, not photo generation.
    gen_photo = bool(_PHOTO_INTENT_RE.search(src)) and not (
        wants_pack and not re.search(r"сгенерируй|нарисуй", src, re.IGNORECASE)
    )
    return MediaIntent(
        wants_photo=gen_photo,
        wants_video=wants_video,
        wants_pack=wants_pack,
    )


def scan_outbox_media(outbox_dir: Path) -> tuple[list[Path], list[Path]]:
    """Return real (photos, videos) currently present and non-empty in outbox/."""
    if not outbox_dir.is_dir():
        return [], []
    photos: list[Path] = []
    videos: list[Path] = []
    for p in sorted(outbox_dir.iterdir()):
        if not p.is_file():
            continue
        try:
            if p.stat().st_size <= 0:
                continue
        except OSError:
            continue
        sfx = p.suffix.lower()
        if sfx in PHOTO_SUFFIXES or sfx == ".webp":
            photos.append(p)
        elif sfx in VIDEO_SUFFIXES:
            videos.append(p)
    return photos, videos


def _format_duration(seconds: float) -> str:
    secs = max(0, int(seconds))
    mins, rem = divmod(secs, 60)
    return f"{mins}:{rem:02d}" if mins else f"{rem}с"


def _format_size(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} МБ"
    return f"{max(1, size // 1024)} КБ"


def render_progress_text(
    intent: MediaIntent,
    *,
    outbox_dir: Path,
    elapsed_seconds: float,
    stage: str = "generate",
) -> str:
    """Render an accurate Telegram HTML progress bar based strictly on observed files/stage."""
    photos, videos = scan_outbox_media(outbox_dir)
    steps: list[tuple[str, bool, str]] = []

    if intent.wants_photo:
        if photos:
            steps.append(("🎨 Фото", True, f"готово ({_format_size(photos[-1])})"))
        else:
            steps.append(("🎨 Фото", False, "генерация…"))

    if intent.wants_video:
        if videos:
            steps.append(("🎬 Видео", True, f"готово ({_format_size(videos[-1])})"))
        elif intent.wants_photo and not photos:
            steps.append(("🎬 Видео", False, "ожидает фото"))
        else:
            steps.append(("🎬 Видео", False, "генерация…"))

    if intent.wants_pack and not (intent.wants_photo or intent.wants_video):
        pack_done = stage in {"deliver", "done"}
        steps.append(("📦 Пак", pack_done, "готов" if pack_done else "сборка…"))

    deliver_done = stage == "done"
    steps.append(("🎙 Озвучка и отправка" if not intent.wants_pack else "📤 Отправка", deliver_done, "готово" if deliver_done else ("в процессе…" if stage == "deliver" else "ожидает")))

    total = max(1, len(steps))
    done_count = sum(1 for _, ok, _ in steps if ok)
    # Show real stage fraction (never fake 99% countdowns)
    filled = round((done_count / total) * 10)
    if done_count == 0 and elapsed_seconds >= 5:
        filled = 1
    bar = "█" * filled + "░" * (10 - filled)

    remaining = [name.split()[-1].lower() for name, ok, _ in steps if not ok]
    rem_label = ", ".join(remaining) if remaining else "завершение"

    lines = [
        f"⏳ <b>Выполняю запрос</b>  <code>[{bar}]</code>  <b>{done_count}/{total}</b>",
        "",
    ]
    for name, ok, status in steps:
        icon = "✅" if ok else ("🔄" if "…" in status else "🕒")
        lines.append(f"{icon} {name}: <i>{status}</i>")
    lines.append("")
    lines.append(f"⏱ Прошло: <b>{_format_duration(elapsed_seconds)}</b> • Осталось: <i>{rem_label}</i>")
    return "\n".join(lines)


def _tg_json_post(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


class MediaProgressTracker:
    """Background progress-bar updater strictly capped to <=1 edit per 8s (zero quota burn)."""

    def __init__(
        self,
        token: str,
        task: dict[str, Any],
        work_dir: Path,
        intent: MediaIntent,
        *,
        initial_delay: float = 5.0,
        min_edit_interval: float = 8.0,
    ) -> None:
        self.token = token
        self.task = task
        self.work_dir = work_dir
        self.outbox_dir = work_dir / "outbox"
        self.intent = intent
        self.initial_delay = initial_delay
        self.min_edit_interval = min_edit_interval
        self._stage = "generate"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._message_id: int | None = None
        self._last_text = ""
        self._last_edit_ts = 0.0
        self._backoff_until = 0.0
        self._started_ts = time.monotonic()

    def start(self) -> None:
        if not self.intent.enabled or self._thread is not None:
            return
        self._started_ts = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def set_stage(self, stage: str) -> None:
        self._stage = stage

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        if self._message_id is not None:
            try:
                _tg_json_post(
                    self.token,
                    "deleteMessage",
                    {"chat_id": int(self.task["chat_id"]), "message_id": self._message_id},
                )
            except Exception:
                LOGGER.debug("Could not delete progress message %s", self._message_id)
            self._message_id = None

    def _sync_once(self) -> None:
        now = time.monotonic()
        if now < self._backoff_until:
            return
        text = render_progress_text(
            self.intent,
            outbox_dir=self.outbox_dir,
            elapsed_seconds=now - self._started_ts,
            stage=self._stage,
        )
        if self._message_id is None:
            payload: dict[str, Any] = {
                "chat_id": int(self.task["chat_id"]),
                "text": text,
                "parse_mode": "HTML",
                "disable_notification": True,
            }
            if self.task.get("thread_id"):
                payload["message_thread_id"] = int(self.task["thread_id"])
            if self.task.get("message_id"):
                payload["reply_to_message_id"] = int(self.task["message_id"])
            res = _tg_json_post(self.token, "sendMessage", payload)
            if res.get("ok"):
                self._message_id = int(res["result"]["message_id"])
                self._last_text = text
                self._last_edit_ts = now
            return

        if text == self._last_text or (now - self._last_edit_ts) < self.min_edit_interval:
            return
        res = _tg_json_post(
            self.token,
            "editMessageText",
            {
                "chat_id": int(self.task["chat_id"]),
                "message_id": self._message_id,
                "text": text,
                "parse_mode": "HTML",
            },
        )
        if res.get("ok"):
            self._last_text = text
            self._last_edit_ts = now

    def _run(self) -> None:
        if self._stop.wait(self.initial_delay):
            return
        while not self._stop.is_set():
            try:
                self._sync_once()
            except urllib.error.HTTPError as err:
                if err.code == 429:
                    try:
                        body = json.loads(err.read().decode("utf-8"))
                        retry_after = float((body.get("parameters") or {}).get("retry_after") or 15)
                    except Exception:
                        retry_after = 15.0
                    self._backoff_until = time.monotonic() + retry_after + 1.0
                else:
                    LOGGER.debug("Progress bar HTTP error %s", err.code)
            except Exception:
                LOGGER.debug("Progress bar update failed", exc_info=True)
            if self._stop.wait(2.0):
                return
