#!/usr/bin/env python3
"""Prepare an 8-10 min Russian voice recording into a clean 24kHz F5-TTS dataset."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import re
import subprocess
import sys


def normalize_audio(src: Path, dst_wav: Path) -> None:
    dst_wav.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-af",
            "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:a",
            "pcm_s16le",
            str(dst_wav),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def detect_silences(wav_path: Path, noise_db: str = "-35dB", min_dur: float = 0.35) -> list[tuple[float, float]]:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(wav_path),
            "-af",
            f"silencedetect=noise={noise_db}:d={min_dur}",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    silences: list[tuple[float, float]] = []
    cur_start: float | None = None
    for line in proc.stderr.splitlines():
        m_start = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m_start:
            cur_start = float(m_start.group(1))
        m_end = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m_end and cur_start is not None:
            silences.append((cur_start, float(m_end.group(1))))
            cur_start = None
    return silences


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
    ).strip()
    return float(out)


def split_chunks(
    wav_path: Path,
    out_dir: Path,
    *,
    min_len: float = 4.0,
    max_len: float = 11.0,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    total_dur = probe_duration(wav_path)
    silences = detect_silences(wav_path)
    cuts = [0.0]
    for s_start, s_end in silences:
        mid = (s_start + s_end) / 2.0
        if mid - cuts[-1] >= min_len:
            cuts.append(mid)
    if total_dur - cuts[-1] >= 2.0:
        cuts.append(total_dur)

    segments: list[tuple[float, float]] = []
    for a, b in zip(cuts, cuts[1:]):
        while b - a > max_len:
            segments.append((a, a + max_len))
            a += max_len
        if b - a >= 2.0:
            segments.append((a, b))

    written: list[Path] = []
    for idx, (start, end) in enumerate(segments, start=1):
        out_wav = out_dir / f"seg_{idx:04d}.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start:.3f}",
                "-to",
                f"{end:.3f}",
                "-i",
                str(wav_path),
                "-c:a",
                "pcm_s16le",
                str(out_wav),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        written.append(out_wav)
    return written


def transcribe_chunks(chunks: list[Path], metadata_csv: Path, model_name: str = "large-v3-turbo") -> None:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper is required for transcription") from exc

    model = WhisperModel(model_name, device="auto", compute_type="float16")
    with metadata_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="|")
        for wav in chunks:
            segs, _ = model.transcribe(str(wav), language="ru", vad_filter=True)
            text = " ".join(s.text.strip() for s in segs if s.text.strip()).strip()
            if text:
                writer.writerow([wav.name, text])


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare 24kHz F5-TTS dataset from raw audio.")
    parser.add_argument("input_audio", type=Path, help="Path to raw recording (.ogg/.wav/.mp3/.m4a)")
    parser.add_argument("output_dir", type=Path, help="Output dataset directory")
    parser.add_argument("--skip-whisper", action="store_true", help="Only slice WAVs without Whisper transcription")
    args = parser.parse_args()

    norm_wav = args.output_dir / "_normalized_24k.wav"
    normalize_audio(args.input_audio, norm_wav)
    wavs_dir = args.output_dir / "wavs"
    chunks = split_chunks(norm_wav, wavs_dir)
    print(f"Prepared {len(chunks)} chunks in {wavs_dir}")
    if not args.skip_whisper:
        meta = args.output_dir / "metadata.csv"
        transcribe_chunks(chunks, meta)
        print(f"Wrote transcripts to {meta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
