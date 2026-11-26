"""2D sprite / pixel-art talking avatar renderer for Telegram circular video notes."""

from __future__ import annotations

import logging
import math
from pathlib import Path
import subprocess
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
import soundfile as sf

from tg_bot.config import FFMPEG_BIN, VOICES_DIR

LOGGER = logging.getLogger("tg_bot.avatar")

AVATAR_SIZE = 384
AVATAR_FPS = 24
EMOTIONS = ("neutral", "happy", "angry", "laugh", "shock", "sad")
STATES = ("closed", "half", "open", "blink")


def _draw_pixel_avatar_sprite(emotion: str, state: str, size: int = AVATAR_SIZE) -> Image.Image:
    """Generate a crisp 32x32 pixel-art character sprite upscaled with NEAREST."""
    grid = 32
    img = Image.new("RGB", (grid, grid))
    draw = ImageDraw.Draw(img)

    bg_colors = {
        "neutral": (24, 30, 48),
        "happy": (26, 48, 42),
        "angry": (54, 20, 24),
        "laugh": (44, 38, 18),
        "shock": (36, 26, 56),
        "sad": (22, 32, 52),
    }
    bg = bg_colors.get(emotion, bg_colors["neutral"])
    draw.rectangle([0, 0, grid - 1, grid - 1], fill=bg)

    # Subtle pixel grid accent
    accent = tuple(min(255, c + 14) for c in bg)
    for y in range(2, grid, 4):
        for x in range(2, grid, 4):
            draw.point((x, y), fill=accent)

    # Shoulders / hoodie
    hoodie = (62, 118, 210) if emotion != "angry" else (180, 58, 62)
    draw.rectangle([7, 23, 24, 31], fill=hoodie)
    draw.rectangle([12, 22, 19, 24], fill=(42, 78, 148))

    # Neck & head
    skin = (240, 198, 162)
    shadow = (212, 165, 132)
    draw.rectangle([14, 20, 17, 23], fill=shadow)
    draw.rectangle([9, 7, 22, 20], fill=skin)
    draw.rectangle([8, 9, 23, 18], fill=skin)

    # Hair
    hair = (42, 34, 48)
    draw.rectangle([8, 5, 23, 9], fill=hair)
    draw.rectangle([7, 7, 9, 13], fill=hair)
    draw.rectangle([22, 7, 24, 13], fill=hair)
    draw.rectangle([10, 4, 21, 6], fill=hair)

    # Eyes & eyebrows
    eye_y = 12
    if state == "blink":
        draw.rectangle([11, eye_y + 1, 13, eye_y + 1], fill=(30, 26, 36))
        draw.rectangle([18, eye_y + 1, 20, eye_y + 1], fill=(30, 26, 36))
    elif emotion == "laugh":
        draw.line([(11, eye_y + 1), (12, eye_y), (13, eye_y + 1)], fill=(30, 26, 36))
        draw.line([(18, eye_y + 1), (19, eye_y), (20, eye_y + 1)], fill=(30, 26, 36))
    else:
        draw.rectangle([11, eye_y, 13, eye_y + 1], fill=(245, 248, 255))
        draw.rectangle([18, eye_y, 20, eye_y + 1], fill=(245, 248, 255))
        pupil = (36, 48, 86) if emotion != "angry" else (160, 26, 30)
        draw.rectangle([12, eye_y, 13, eye_y + 1], fill=pupil)
        draw.rectangle([18, eye_y, 19, eye_y + 1], fill=pupil)

    # Eyebrows
    brow_color = (34, 26, 36)
    if emotion == "angry":
        draw.line([(10, 10), (14, 11)], fill=brow_color)
        draw.line([(17, 11), (21, 10)], fill=brow_color)
    elif emotion in ("sad", "shock"):
        draw.line([(10, 11), (13, 10)], fill=brow_color)
        draw.line([(18, 10), (21, 11)], fill=brow_color)
    else:
        draw.rectangle([11, 10, 13, 10], fill=brow_color)
        draw.rectangle([18, 10, 20, 10], fill=brow_color)

    # Mouth
    mouth_dark = (88, 32, 38)
    tongue = (214, 92, 104)
    if state == "closed" or state == "blink":
        if emotion in ("happy", "laugh"):
            draw.line([(13, 17), (15, 18), (16, 18), (18, 17)], fill=mouth_dark)
        elif emotion in ("sad", "angry"):
            draw.line([(13, 18), (15, 17), (16, 17), (18, 18)], fill=mouth_dark)
        else:
            draw.rectangle([14, 17, 17, 17], fill=mouth_dark)
    elif state == "half":
        draw.rectangle([14, 16, 17, 18], fill=mouth_dark)
        draw.rectangle([15, 17, 16, 18], fill=tongue)
    else:  # open
        if emotion == "shock":
            draw.rectangle([14, 16, 17, 19], fill=mouth_dark)
        else:
            draw.rectangle([13, 16, 18, 19], fill=mouth_dark)
            draw.rectangle([14, 18, 17, 19], fill=tongue)

    return img.resize((size, size), resample=Image.Resampling.NEAREST)


def ensure_default_avatar_sprites(avatar_dir: Path) -> Path:
    """Create starter pixel-art sprite set if the directory has no sprites yet."""
    avatar_dir.mkdir(parents=True, exist_ok=True)
    if (avatar_dir / "neutral_closed.png").is_file() and (avatar_dir / "neutral_open.png").is_file():
        return avatar_dir
    for emotion in EMOTIONS:
        for state in STATES:
            target = avatar_dir / f"{emotion}_{state}.png"
            if not target.is_file():
                _draw_pixel_avatar_sprite(emotion, state).save(target, format="PNG")
    return avatar_dir


def resolve_avatar_dir(
    profile_name: str,
    profiles_data: dict[str, Any],
    *,
    persona_name: str | None = None,
) -> Path:
    """Resolve sprite directory for persona or voice profile, falling back to default pixel-art."""
    profiles = profiles_data.get("profiles") or {}
    candidates: list[str] = []
    if persona_name and persona_name.strip():
        candidates.append(persona_name.strip())
    if profile_name and profile_name.strip() and profile_name.strip() not in candidates:
        candidates.append(profile_name.strip())

    for name in candidates:
        entry = profiles.get(name) or {}
        rel_dir = entry.get("avatar_dir")
        if rel_dir:
            candidate = VOICES_DIR / str(rel_dir)
            if candidate.is_dir() and (candidate / "neutral_closed.png").is_file():
                return candidate
        custom_dir = VOICES_DIR / "avatars" / name
        if custom_dir.is_dir() and (custom_dir / "neutral_closed.png").is_file():
            return custom_dir

    default_dir = VOICES_DIR / "avatars" / "default"
    return ensure_default_avatar_sprites(default_dir)


def _load_sprite_frames(
    avatar_dir: Path, emotion: str, size: int = AVATAR_SIZE
) -> dict[str, np.ndarray]:
    """Load RGB numpy arrays for closed/half/open/blink states with clean fallbacks."""
    emo = emotion if emotion in EMOTIONS else "neutral"

    def _pick(state: str, fallbacks: list[str]) -> np.ndarray:
        candidates = [f"{emo}_{state}.png", f"neutral_{state}.png", *fallbacks]
        for fname in candidates:
            path = avatar_dir / fname
            if path.is_file():
                with Image.open(path) as im:
                    rgb = im.convert("RGB")
                    if rgb.size != (size, size):
                        # Use NEAREST to preserve crisp pixel art edges if upscaling
                        resample = (
                            Image.Resampling.NEAREST
                            if rgb.width <= 128
                            else Image.Resampling.LANCZOS
                        )
                        rgb = rgb.resize((size, size), resample=resample)
                    return np.asarray(rgb, dtype=np.uint8)
        return np.asarray(_draw_pixel_avatar_sprite(emo, state, size), dtype=np.uint8)

    closed = _pick("closed", [f"{emo}_open.png", "neutral_closed.png"])
    open_f = _pick("open", [f"{emo}_closed.png", "neutral_open.png"])
    half = _pick("half", [f"{emo}_open.png", "neutral_half.png", "neutral_open.png"])
    blink = _pick("blink", [f"{emo}_closed.png", "neutral_blink.png", "neutral_closed.png"])
    return {"closed": closed, "half": half, "open": open_f, "blink": blink}


def compute_mouth_states(
    audio_path: Path, *, fps: int = AVATAR_FPS
) -> tuple[list[str], float]:
    """Analyze audio RMS envelope per frame to drive mouth open/half/closed + blinks."""
    data, sr = sf.read(str(audio_path), dtype="float32", always_2d=False)
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)

    total_samples = len(data)
    duration = max(0.5, total_samples / max(1, sr))
    n_frames = max(1, int(math.ceil(duration * fps)))
    samples_per_frame = max(1, int(sr / fps))

    rms_values: list[float] = []
    for i in range(n_frames):
        start = i * samples_per_frame
        end = min(total_samples, start + samples_per_frame)
        if start >= total_samples:
            rms_values.append(0.0)
        else:
            chunk = data[start:end]
            rms_values.append(float(np.sqrt(np.mean(chunk * chunk))))

    peak = max(rms_values) if rms_values else 0.0
    low_thr = max(0.008, peak * 0.14)
    high_thr = max(0.025, peak * 0.42)

    states: list[str] = []
    blink_period = int(fps * 3.2)
    for i, rms in enumerate(rms_values):
        if rms < low_thr:
            # Periodic natural blink during pauses
            if blink_period > 0 and 0 <= (i % blink_period) <= 2:
                states.append("blink")
            else:
                states.append("closed")
        elif rms < high_thr:
            states.append("half")
        else:
            states.append("open")
    return states, duration


def render_talking_avatar_mp4(
    audio_path: Path,
    output_mp4: Path,
    *,
    avatar_dir: Path,
    emotion: str = "neutral",
    size: int = AVATAR_SIZE,
    fps: int = AVATAR_FPS,
) -> tuple[Path, int]:
    """Render a square H.264/AAC talking-avatar MP4 suitable for Telegram sendVideoNote."""
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    sprites = _load_sprite_frames(avatar_dir, emotion, size=size)
    states, duration = compute_mouth_states(audio_path, fps=fps)

    # Pre-compute slight 3px upward bounce frame for loud 'open' syllables
    open_bounce = np.roll(sprites["open"], -3, axis=0)
    open_bounce[-3:, :, :] = sprites["open"][-1:, :, :]

    cmd = [
        FFMPEG_BIN,
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{size}x{size}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-i",
        str(audio_path),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-ar",
        "44100",
        "-ac",
        "1",
        "-movflags",
        "+faststart",
        "-shortest",
        str(output_mp4),
    ]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        for idx, state in enumerate(states):
            if state == "open" and (idx % 2 == 0):
                frame = open_bounce
            else:
                frame = sprites.get(state, sprites["closed"])
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    stderr_bytes = proc.stderr.read() if proc.stderr else b""
    ret = proc.wait(timeout=60)
    if ret != 0 or not output_mp4.is_file():
        raise RuntimeError(
            f"Avatar ffmpeg render failed ({ret}): {stderr_bytes.decode(errors='replace')[-400:]}"
        )
    return output_mp4, max(1, int(round(duration)))
