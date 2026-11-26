"""Local faster-whisper speech transcription with automatic CUDA/CPU fallback."""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import threading

from tg_bot.config import FFMPEG_BIN, WHISPER_MODEL

LOGGER = logging.getLogger("tg_bot.transcribe")
_MODEL_LOCK = threading.Lock()
_CACHED_MODEL = None
_FORCE_CPU = False


def _get_model(*, force_cpu: bool = False):
    global _CACHED_MODEL, _FORCE_CPU
    if force_cpu and not _FORCE_CPU:
        _FORCE_CPU = True
        _CACHED_MODEL = None
    if _CACHED_MODEL is None:
        with _MODEL_LOCK:
            if _CACHED_MODEL is None:
                from faster_whisper import WhisperModel

                if not _FORCE_CPU:
                    try:
                        import torch

                        if torch.cuda.is_available():
                            _CACHED_MODEL = WhisperModel(
                                WHISPER_MODEL, device="cuda", compute_type="float16"
                            )
                            LOGGER.info("Loaded Whisper model %s on CUDA (float16)", WHISPER_MODEL)
                            return _CACHED_MODEL
                    except Exception as exc:
                        LOGGER.warning("CUDA Whisper load failed (%s), falling back to CPU", exc)

                _FORCE_CPU = True
                _CACHED_MODEL = WhisperModel(
                    WHISPER_MODEL, device="cpu", compute_type="int8"
                )
                LOGGER.info("Loaded Whisper model %s on CPU (int8)", WHISPER_MODEL)
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
        import soundfile as sf

        audio_data, _ = sf.read(str(source), dtype="float32", always_2d=False)
        if getattr(audio_data, "ndim", 1) > 1:
            audio_data = audio_data.mean(axis=1)

        kwargs: dict[str, object] = {"beam_size": 5, "vad_filter": True}
        if language:
            kwargs["language"] = language

        try:
            model = _get_model()
            segments, _ = model.transcribe(audio_data, **kwargs)
            return " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as exc:
            if not _FORCE_CPU and ("cublas" in str(exc).lower() or "cuda" in str(exc).lower()):
                LOGGER.warning("Whisper CUDA runtime error (%s), retrying on CPU int8", exc)
                model = _get_model(force_cpu=True)
                segments, _ = model.transcribe(audio_data, **kwargs)
                return " ".join(seg.text.strip() for seg in segments).strip()
            raise
    finally:
        if wav_path.is_file():
            wav_path.unlink()
