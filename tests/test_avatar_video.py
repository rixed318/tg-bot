from __future__ import annotations

import math
from pathlib import Path
import struct
import wave

from tg_bot import avatar_video
from tg_bot.media_tools import parse_agent_response
from tg_bot.voice_tts import probe_duration_seconds


def _write_modulated_wav(path: Path, duration_s: float = 1.4, sr: int = 24000) -> Path:
    n_samples = int(duration_s * sr)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = bytearray()
        for i in range(n_samples):
            # Alternate speech-like bursts and silence
            env = 0.8 if (i // 6000) % 2 == 0 else 0.0
            val = int(14000 * env * math.sin(2.0 * math.pi * 220.0 * i / sr))
            frames.extend(struct.pack("<h", val))
        wf.writeframes(bytes(frames))
    return path


def test_ensure_default_sprites_and_lip_sync_render(tmp_path: Path) -> None:
    sprites_dir = avatar_video.ensure_default_avatar_sprites(tmp_path / "avatar")
    assert (sprites_dir / "neutral_closed.png").is_file()
    assert (sprites_dir / "angry_open.png").is_file()

    wav = _write_modulated_wav(tmp_path / "speech.wav", duration_s=1.2)
    states, dur = avatar_video.compute_mouth_states(wav, fps=24)
    assert dur >= 1.0
    assert "open" in states
    assert any(s in ("closed", "blink") for s in states)

    mp4_out, out_dur = avatar_video.render_talking_avatar_mp4(
        wav, tmp_path / "avatar.mp4", avatar_dir=sprites_dir, emotion="angry"
    )
    assert mp4_out.is_file()
    assert mp4_out.stat().st_size > 5000
    assert out_dur >= 1
    assert probe_duration_seconds(mp4_out) >= 1


def test_parse_emotion_directive(tmp_path: Path) -> None:
    parsed = parse_agent_response("Ну ты даёшь, конечно!\n[[emotion:laugh]]", tmp_path)
    assert parsed.text == "Ну ты даёшь, конечно!"
    assert parsed.emotion == "laugh"


def test_resolve_avatar_dir_prefers_persona_over_voice() -> None:
    profiles = {
        "profiles": {
            "anya": {"avatar_dir": "avatars/anya"},
            "arsen": {"audio": "arsen.wav"},
        }
    }
    resolved = avatar_video.resolve_avatar_dir("arsen", profiles, persona_name="anya")
    assert resolved.name == "anya"
