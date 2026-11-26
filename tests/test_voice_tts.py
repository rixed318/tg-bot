from __future__ import annotations

import math
from pathlib import Path
import struct
import wave

from tg_bot import voice_tts


def _write_sine_wav(path: Path, duration_s: float = 0.6, sr: int = 24000) -> Path:
    n_samples = int(duration_s * sr)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = bytearray()
        for i in range(n_samples):
            val = int(12000 * math.sin(2.0 * math.pi * 440.0 * i / sr))
            frames.extend(struct.pack("<h", val))
        wf.writeframes(bytes(frames))
    return path


def test_normalize_text_for_speech_cleans_markdown_emojis_and_3d_terms() -> None:
    raw = "**Смотри**: в `Blender` на GPU Cycles 3 раза вылетело! https://example.com 😎🔥 [[sticker]]"
    spoken = voice_tts.normalize_text_for_speech(raw)
    assert "**" not in spoken
    assert "`" not in spoken
    assert "https://" not in spoken
    assert "[[" not in spoken
    assert "😎" not in spoken
    assert "блэндер" in spoken.lower()
    assert "сайклз" in spoken.lower()
    assert "три" in spoken.lower()


def test_convert_to_telegram_ogg_and_probe_duration(tmp_path: Path) -> None:
    wav_path = _write_sine_wav(tmp_path / "tone.wav", duration_s=1.2)
    ogg_path = tmp_path / "tone.ogg"
    voice_tts.convert_to_telegram_ogg(wav_path, ogg_path)
    assert ogg_path.is_file()
    assert ogg_path.stat().st_size > 500
    assert voice_tts.probe_duration_seconds(ogg_path) >= 1


def test_custom_voice_profile_activation_and_reset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(voice_tts, "VOICES_DIR", tmp_path)
    monkeypatch.setattr(voice_tts, "VOICE_PROFILES_FILE", tmp_path / "profiles.json")
    monkeypatch.setattr(voice_tts, "transcribe_audio", lambda *a, **kw: "Тестовый голос")

    sample = _write_sine_wav(tmp_path / "raw_user.wav", duration_s=1.0)
    wav_out, transcript = voice_tts.set_custom_voice_from_audio(sample)
    assert wav_out.is_file()
    assert transcript == "Тестовый голос"

    name, active_wav, active_txt = voice_tts.get_active_voice_reference()
    assert name == "custom"
    assert active_wav == wav_out
    assert active_txt == "Тестовый голос"

    assert voice_tts.reset_active_voice() == "default"
    profiles = voice_tts.load_voice_profiles()
    assert profiles["active"] == "default"
