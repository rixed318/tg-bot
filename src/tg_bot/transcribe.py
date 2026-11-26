"""Local faster-whisper speech transcription with in-process model caching."""

from __future__ import annotations

from pathlib import Path
import subprocess
import threading

from tg_bot.config import FFMPEG_BIN

_MODEL_LOCK = threading.Lock()
_CACHED_MODEL = None


def _get_model():
    global _CACHED_MODEL
    if _CACHED_MODEL is None:
        with _MODEL_LOCK:
            if _CACHED_MODEL is None:
                from faster_whisper import WhisperModel

                _CACHED_MODEL = WhisperModel("base", device="cpu", compute_type="int8")
    return _CACHED_MODEL


def transcribe_audio(audio_path: Path, *, language: str | None = None) -> str:
    """Convert any audio/video note to 16kHz mono WAV and transcribe via faster-whisper."""
    wav_path = audio_path.parent / f"{audio_path.stem}_transcribe.wav"
    try:
        subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-i",
                str(audio_path),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(wav_path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        source = wav_path
    except Exception:
        source = audio_path

    try:
        model = _get_model()
        kwargs: dict[str, object] = {"beam_size": 2}
        if language:
            kwargs["language"] = language
        segments, _ = model.transcribe(str(source), **kwargs)
        return " ".join(seg.text.strip() for seg in segments).strip()
    finally:
        if wav_path.is_file():
            wav_path.unlink()
