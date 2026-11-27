"""Deterministic local singing-cover pipeline: stems -> Seed-VC -> mix -> chunks."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from tg_bot.config import (
    FFMPEG_BIN,
    SINGING_CHUNK_SECONDS,
    SINGING_DIFFUSION_STEPS,
    SINGING_ENGINE_PYTHON,
    SINGING_ENGINE_ROOT,
    VOICES_DIR,
)

LOGGER = logging.getLogger("tg_bot.singing_cover")

SINGING_REQUEST = re.compile(
    r"\b(спой|спеть|перепой|перепеть|зачитай|зачитать|исполни|исполнить|кавер(?:ом|а|ы)?|под\s+бит)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SingingCoverArtifacts:
    mixed_audio: Path
    converted_vocal: Path
    mixed_chunks: tuple[Path, ...]
    vocal_chunks: tuple[Path, ...]


def is_singing_cover_request(messages: list[dict[str, Any]]) -> bool:
    text = "\n".join(str(item.get("text") or "") for item in messages)
    return bool(SINGING_REQUEST.search(text))


def resolve_singing_reference(
    bot_settings: dict[str, Any], profiles_data: dict[str, Any]
) -> tuple[str, Path]:
    profiles = profiles_data.get("profiles") or {}
    profile_name = str(
        bot_settings.get("voice_profile") or profiles_data.get("active") or "default"
    ).strip()
    entry = profiles.get(profile_name) or profiles.get("default") or {}
    audio_name = str(entry.get("audio") or "default.wav")
    audio_path = VOICES_DIR / audio_name
    if not audio_path.is_file() or audio_path.stat().st_size < 1000:
        raise FileNotFoundError(f"Singing reference voice is missing: {audio_path}")
    return profile_name, audio_path


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 3600) -> None:
    LOGGER.info("Running singing stage: %s", command[1] if len(command) > 1 else command[0])
    # Model hosts work reliably over the direct connection on this machine; the
    # desktop proxy intermittently stalls large Hugging Face and PyTorch files.
    child_env = os.environ.copy()
    for proxy_name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        child_env.pop(proxy_name, None)
    child_env.setdefault(
        "SEED_VC_RMVPE_PATH",
        str(SINGING_ENGINE_ROOT / "checkpoints/rmvpe/rmvpe.pt"),
    )
    child_env.setdefault(
        "SEED_VC_CAMPPLUS_PATH",
        str(SINGING_ENGINE_ROOT / "checkpoints/campplus/campplus_cn_common.bin"),
    )
    child_env.setdefault("HF_HUB_DISABLE_XET", "1")
    result = subprocess.run(
        command,
        cwd=cwd,
        env=child_env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "")[-1200:]
        raise RuntimeError(f"Singing stage failed ({result.returncode}): {details}")


def split_audio(
    source: Path,
    output_dir: Path,
    *,
    prefix: str,
    segment_seconds: int = SINGING_CHUNK_SECONDS,
) -> tuple[Path, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / f"{prefix}_%02d.wav"
    _run(
        [
            FFMPEG_BIN,
            "-y",
            "-i",
            str(source),
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-reset_timestamps",
            "1",
            "-c:a",
            "pcm_s16le",
            str(pattern),
        ],
        timeout=300,
    )
    chunks = tuple(sorted(output_dir.glob(f"{prefix}_*.wav")))
    if not chunks:
        raise RuntimeError(f"Audio split produced no chunks for {source}")
    return chunks


def build_singing_cover(
    source_audio: Path,
    reference_voice: Path,
    output_dir: Path,
    *,
    diffusion_steps: int = SINGING_DIFFUSION_STEPS,
    segment_seconds: int = SINGING_CHUNK_SECONDS,
) -> SingingCoverArtifacts:
    if not SINGING_ENGINE_PYTHON.is_file():
        raise RuntimeError(f"Singing engine is not installed: {SINGING_ENGINE_PYTHON}")

    output_dir.mkdir(parents=True, exist_ok=True)
    normalized = output_dir / "source.wav"
    _run(
        [
            FFMPEG_BIN,
            "-y",
            "-i",
            str(source_audio),
            "-vn",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(normalized),
        ],
        timeout=300,
    )

    stems_root = output_dir / "stems"
    _run(
        [
            str(SINGING_ENGINE_PYTHON),
            "-m",
            "demucs.separate",
            "--two-stems=vocals",
            "-n",
            "htdemucs",
            "--device",
            "cuda",
            "-o",
            str(stems_root),
            str(normalized),
        ],
        cwd=SINGING_ENGINE_ROOT,
        timeout=1800,
    )
    stem_dir = stems_root / "htdemucs" / normalized.stem
    vocals = stem_dir / "vocals.wav"
    instrumental = stem_dir / "no_vocals.wav"
    if not vocals.is_file() or not instrumental.is_file():
        raise RuntimeError("Demucs did not produce vocals and no_vocals stems")

    converted_dir = output_dir / "converted"
    checkpoint = (
        SINGING_ENGINE_ROOT
        / "checkpoints/svc/DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth"
    )
    config = (
        SINGING_ENGINE_ROOT
        / "checkpoints/svc/config_dit_mel_seed_uvit_whisper_base_f0_44k.yml"
    )
    _run(
        [
            str(SINGING_ENGINE_PYTHON),
            "inference.py",
            "--source",
            str(vocals),
            "--target",
            str(reference_voice),
            "--output",
            str(converted_dir),
            "--diffusion-steps",
            str(diffusion_steps),
            "--length-adjust",
            "1.0",
            "--inference-cfg-rate",
            "0.7",
            "--f0-condition",
            "True",
            "--auto-f0-adjust",
            "True",
            "--semi-tone-shift",
            "0",
            "--checkpoint",
            str(checkpoint),
            "--config",
            str(config),
            "--fp16",
            "True",
        ],
        cwd=SINGING_ENGINE_ROOT,
        timeout=3600,
    )
    converted_candidates = sorted(converted_dir.glob("vc_*.wav"))
    if len(converted_candidates) != 1:
        raise RuntimeError(
            f"Seed-VC produced {len(converted_candidates)} converted vocal files"
        )
    converted_vocal = converted_candidates[0]

    mixed_audio = output_dir / "singing_cover.wav"
    _run(
        [
            FFMPEG_BIN,
            "-y",
            "-i",
            str(instrumental),
            "-i",
            str(converted_vocal),
            "-filter_complex",
            "[0:a]volume=0.92[inst];[1:a]volume=1.12[voc];"
            "[inst][voc]amix=inputs=2:duration=longest:normalize=0,"
            "alimiter=limit=0.97[out]",
            "-map",
            "[out]",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(mixed_audio),
        ],
        timeout=600,
    )

    chunks_dir = output_dir / "chunks"
    mixed_chunks = split_audio(
        mixed_audio, chunks_dir, prefix="mix", segment_seconds=segment_seconds
    )
    vocal_chunks = split_audio(
        converted_vocal, chunks_dir, prefix="vocal", segment_seconds=segment_seconds
    )
    if len(mixed_chunks) != len(vocal_chunks):
        raise RuntimeError(
            f"Mixed/vocal chunk count differs: {len(mixed_chunks)} != {len(vocal_chunks)}"
        )
    return SingingCoverArtifacts(
        mixed_audio=mixed_audio,
        converted_vocal=converted_vocal,
        mixed_chunks=mixed_chunks,
        vocal_chunks=vocal_chunks,
    )
