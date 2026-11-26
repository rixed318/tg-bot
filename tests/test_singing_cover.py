from __future__ import annotations

from pathlib import Path
import struct
import wave

from tg_bot import singing_cover


def _write_wav(path: Path, duration: float = 2.2, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"".join(struct.pack("<h", 0) for _ in range(int(duration * rate))))
    return path


def test_detects_singing_request_across_batched_messages() -> None:
    messages = [
        {"text": "Jarvis - TAU"},
        {"text": "Спой полностью этот трек под бит"},
        {"text": "[К сообщению прикреплено: аудиотрек]"},
    ]
    assert singing_cover.is_singing_cover_request(messages)
    assert not singing_cover.is_singing_cover_request([{"text": "Как тебе трек?"}])


def test_split_audio_respects_video_note_limit(tmp_path: Path) -> None:
    source = _write_wav(tmp_path / "source.wav")
    chunks = singing_cover.split_audio(
        source, tmp_path / "chunks", prefix="part", segment_seconds=1
    )
    assert len(chunks) == 3
    assert all(path.is_file() and path.stat().st_size > 100 for path in chunks)


def test_resolve_singing_reference_uses_selected_voice(
    tmp_path: Path, monkeypatch
) -> None:
    reference = _write_wav(tmp_path / "anime.wav", duration=0.2)
    monkeypatch.setattr(singing_cover, "VOICES_DIR", tmp_path)
    profiles = {
        "active": "default",
        "profiles": {
            "anime": {"audio": reference.name},
            "default": {"audio": "default.wav"},
        },
    }
    name, path = singing_cover.resolve_singing_reference(
        {"voice_profile": "anime"}, profiles
    )
    assert name == "anime"
    assert path == reference
