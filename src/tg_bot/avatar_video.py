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
    speech_display: str = "quality",
) -> Path:
    """Resolve sprite directory for persona or voice profile, respecting speech_display mode."""
    profiles = profiles_data.get("profiles") or {}
    candidates: list[str] = []
    if persona_name and persona_name.strip():
        candidates.append(persona_name.strip())
    if profile_name and profile_name.strip() and profile_name.strip() not in candidates:
        candidates.append(profile_name.strip())

    variant = speech_display.strip().lower()
    for name in candidates:
        if variant and variant not in ("quality", "default", "normal"):
            variant_dir = VOICES_DIR / "avatars" / f"{name}_{variant}"
            if variant_dir.is_dir() and (variant_dir / "neutral_closed.png").is_file():
                return variant_dir
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
    """Load RGB numpy arrays for closed/half/open/blink + optional wink/brows_up."""
    emo = emotion if emotion in EMOTIONS else "neutral"

    def _load_img(path: Path) -> np.ndarray:
        with Image.open(path) as im:
            rgb = im.convert("RGB")
            if rgb.size != (size, size):
                resample = (
                    Image.Resampling.NEAREST
                    if rgb.width <= 128
                    else Image.Resampling.LANCZOS
                )
                rgb = rgb.resize((size, size), resample=resample)
            return np.asarray(rgb, dtype=np.uint8)

    def _pick(state: str, fallbacks: list[str]) -> np.ndarray:
        candidates = [f"{emo}_{state}.png", f"neutral_{state}.png", *fallbacks]
        for fname in candidates:
            path = avatar_dir / fname
            if path.is_file():
                return _load_img(path)
        return np.asarray(_draw_pixel_avatar_sprite(emo, state, size), dtype=np.uint8)

    def _pick_optional(candidates: list[str]) -> np.ndarray | None:
        for fname in candidates:
            path = avatar_dir / fname
            if path.is_file():
                return _load_img(path)
        return None

    closed = _pick("closed", [f"{emo}_open.png", "neutral_closed.png"])
    open_f = _pick("open", [f"{emo}_closed.png", "neutral_open.png"])
    half = _pick("half", [f"{emo}_open.png", "neutral_half.png", "neutral_open.png"])
    blink = _pick("blink", [f"{emo}_closed.png", "neutral_blink.png", "neutral_closed.png"])
    frames: dict[str, np.ndarray] = {
        "closed": closed,
        "half": half,
        "open": open_f,
        "blink": blink,
    }
    wink = _pick_optional([f"{emo}_wink.png", "neutral_wink.png"])
    if wink is not None:
        frames["wink"] = wink
    wink_open = _pick_optional([f"{emo}_wink_open.png", "flirty_wink_open.png"])
    if wink_open is not None:
        frames["wink_open"] = wink_open
    brows_up = _pick_optional([f"{emo}_brows_up.png", "brows_up_open.png", "neutral_brows_up.png"])
    if brows_up is not None:
        frames["brows_up"] = brows_up
    return frames


def _load_rig_hand(avatar_dir: Path, size: int = AVATAR_SIZE) -> Image.Image | None:
    """Load optional transparent hand/prop overlay from _rigs/<base_char>/hand_wave.png."""
    base_name = avatar_dir.name.split("_", 1)[0]
    rig_dir = VOICES_DIR / "avatars" / "_rigs" / base_name
    for fname in ("hand_mic.png", "hand_wave.png"):
        path = rig_dir / fname
        if path.is_file():
            with Image.open(path) as im:
                rgba = im.convert("RGBA")
                target = max(96, size // 2)
                if rgba.size != (target, target):
                    rgba = rgba.resize((target, target), Image.Resampling.NEAREST)
                return rgba
    return None


def compute_smoothed_envelope(
    audio_path: Path, *, fps: int = AVATAR_FPS
) -> tuple[list[float], list[str], float]:
    """Analyze audio RMS envelope with attack/release smoothing for fluid lip-sync."""
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

    non_zero = [v for v in rms_values if v > 1e-5]
    ref_peak = float(np.percentile(non_zero, 95)) if non_zero else 0.0
    ref_peak = max(ref_peak, 0.03)
    noise_floor = max(0.005, ref_peak * 0.08)

    smoothed: list[float] = []
    prev = 0.0
    for rms in rms_values:
        norm = max(0.0, min(1.0, (rms - noise_floor) / max(1e-5, ref_peak - noise_floor)))
        alpha = 0.58 if norm >= prev else 0.32
        cur = prev + alpha * (norm - prev)
        smoothed.append(cur)
        prev = cur

    states: list[str] = []
    blink_period = int(fps * 3.4)
    for i, level in enumerate(smoothed):
        phase = i % blink_period if blink_period > 0 else -1
        if level < 0.12:
            if 0 <= phase <= 3:
                states.append("blink")
            else:
                states.append("closed")
        elif level < 0.48:
            states.append("half")
        else:
            states.append("open")
    return smoothed, states, duration


def compute_mouth_states(
    audio_path: Path, *, fps: int = AVATAR_FPS
) -> tuple[list[str], float]:
    """Analyze audio RMS envelope per frame to drive mouth open/half/closed + blinks."""
    _, states, duration = compute_smoothed_envelope(audio_path, fps=fps)
    return states, duration


def _blend_mouth_frame(
    sprites: dict[str, np.ndarray],
    level: float,
    blink_weight: float,
    wink_weight: float = 0.0,
    brows_weight: float = 0.0,
) -> np.ndarray:
    """Smoothly blend closed/half/open + brows_up + blink/wink expressions."""
    lvl = max(0.0, min(1.0, level))
    if lvl < 0.5:
        t = lvl / 0.5
        a, b = sprites["closed"], sprites["half"]
    else:
        t = (lvl - 0.5) / 0.5
        a, b = sprites["half"], sprites["open"]

    blended = a.astype(np.float32) * (1.0 - t) + b.astype(np.float32) * t
    if brows_weight > 0.01 and "brows_up" in sprites:
        brw = max(0.0, min(0.85, brows_weight))
        blended = blended * (1.0 - brw) + sprites["brows_up"].astype(np.float32) * brw
    if blink_weight > 0.01:
        bw = max(0.0, min(1.0, blink_weight))
        blended = blended * (1.0 - bw) + sprites["blink"].astype(np.float32) * bw
    if wink_weight > 0.01 and ("wink" in sprites or "wink_open" in sprites):
        ww = max(0.0, min(1.0, wink_weight))
        if "wink_open" in sprites and "wink" in sprites:
            wink_img = sprites["wink"].astype(np.float32) * (1.0 - lvl) + sprites["wink_open"].astype(np.float32) * lvl
        else:
            wink_img = sprites.get("wink_open", sprites.get("wink")).astype(np.float32)
        blended = blended * (1.0 - ww) + wink_img * ww
    return np.clip(blended, 0, 255).astype(np.uint8)


def _apply_smooth_motion(
    frame: np.ndarray,
    *,
    t_sec: float,
    duration: float,
    energy: float,
    emotion: str,
    camera: str = "auto",
    size: int,
    hand_overlay: Image.Image | None = None,
) -> bytes:
    """Apply subtle breathing/speech nod plus optional camera pan/zoom without black edges."""
    emo_bounce = 1.2 if emotion in ("happy", "laugh") else (0.7 if emotion == "sad" else 1.0)
    progress = min(1.0, max(0.0, t_sec / max(0.5, duration)))
    ease = 0.5 - 0.5 * math.cos(math.pi * progress)

    cam = (camera or "auto").strip().lower()
    cam_dx = 0.0
    cam_dy = 0.0
    cam_zoom = 0.0
    if cam in ("pan_left", "left"):
        cam_dx = 12.0 * (1.0 - 2.0 * ease)
        cam_zoom = 0.065
    elif cam in ("pan_right", "right"):
        cam_dx = -12.0 * (1.0 - 2.0 * ease)
        cam_zoom = 0.065
    elif cam in ("pan_up", "up"):
        cam_dy = 12.0 * (1.0 - 2.0 * ease)
        cam_zoom = 0.065
    elif cam in ("pan_down", "down"):
        cam_dy = -12.0 * (1.0 - 2.0 * ease)
        cam_zoom = 0.065
    elif cam in ("zoom_in", "in"):
        cam_zoom = 0.12 * ease
    elif cam in ("zoom_out", "out"):
        cam_zoom = 0.12 * (1.0 - ease)
    elif cam == "shake":
        cam_dx = 4.5 * math.sin(2.0 * math.pi * 3.5 * t_sec) * (0.4 + 0.6 * energy)
        cam_dy = 3.5 * math.cos(2.0 * math.pi * 2.8 * t_sec) * (0.4 + 0.6 * energy)
        cam_zoom = 0.05

    angle_deg = 0.0
    if cam in ("tilt_left", "nod_left"):
        angle_deg = -3.2 * ease
        cam_zoom = max(cam_zoom, 0.07)
    elif cam in ("tilt_right", "nod_right"):
        angle_deg = 3.2 * ease
        cam_zoom = max(cam_zoom, 0.07)
    elif cam == "nod":
        cam_dy = 7.0 * math.sin(2.0 * math.pi * 2.0 * progress)
        cam_zoom = max(cam_zoom, 0.06)
    elif emotion in ("happy", "laugh"):
        angle_deg = 1.4 * math.sin(2.0 * math.pi * 0.55 * t_sec) * (0.35 + 0.65 * energy)
    else:
        angle_deg = 0.8 * math.sin(2.0 * math.pi * 0.4 * t_sec) * energy

    scale = 1.03 + cam_zoom + 0.004 * math.sin(2.0 * math.pi * 0.35 * t_sec) + 0.008 * energy * emo_bounce
    dy = cam_dy - 1.6 * energy * emo_bounce

    cx = size * 0.5
    cy = size * 0.5
    inv_scale = 1.0 / scale
    rad = math.radians(angle_deg)
    cos_a = math.cos(rad) * inv_scale
    sin_a = math.sin(rad) * inv_scale
    tx = cx + cam_dx
    ty = cy + dy
    a = cos_a
    b = -sin_a
    c = cx - (a * tx + b * ty)
    d = sin_a
    e = cos_a
    f = cy - (d * tx + e * ty)

    pil_img = Image.fromarray(frame, mode="RGB")
    moved = pil_img.transform(
        (size, size),
        Image.Transform.AFFINE,
        (a, b, c, d, e, f),
        resample=Image.Resampling.BILINEAR,
    )
    if hand_overlay is not None and (emotion in ("happy", "laugh") or energy > 0.25):
        hx = size - hand_overlay.width - 8
        hy = int(size - hand_overlay.height + 10 - 10 * energy - 4 * math.sin(2.0 * math.pi * 0.9 * t_sec))
        moved.paste(hand_overlay, (hx, hy), hand_overlay)
    return moved.tobytes()


def render_talking_avatar_mp4(
    audio_path: Path,
    output_mp4: Path,
    *,
    avatar_dir: Path,
    output_audio_path: Path | None = None,
    emotion: str = "neutral",
    camera: str = "auto",
    size: int = AVATAR_SIZE,
    fps: int = AVATAR_FPS,
) -> tuple[Path, int]:
    """Render a talking avatar, optionally driving its mouth from an isolated vocal."""
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    sprites = _load_sprite_frames(avatar_dir, emotion, size=size)
    hand_overlay = _load_rig_hand(avatar_dir, size=size)
    levels, states, duration = compute_smoothed_envelope(audio_path, fps=fps)

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
        str(output_audio_path or audio_path),
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
    blink_curve = (0.35, 0.78, 1.0, 0.78, 0.35)
    wink_curve = (0.35, 0.8, 1.0, 1.0, 0.8, 0.35)
    blink_period = max(24, int(fps * 3.1))
    wink_period = max(48, int(fps * 6.8))
    try:
        for idx, (level, state) in enumerate(zip(levels, states)):
            b_phase = idx % blink_period
            blink_w = blink_curve[b_phase] if b_phase < len(blink_curve) and level < 0.38 else (
                1.0 if state == "blink" else 0.0
            )
            wink_w = 0.0
            if emotion in ("happy", "laugh") and ("wink" in sprites or "wink_open" in sprites):
                w_phase = (idx + fps) % wink_period
                if w_phase < len(wink_curve):
                    wink_w = wink_curve[w_phase]
                    blink_w = 0.0
            brows_w = max(0.0, (level - 0.62) / 0.38) if "brows_up" in sprites else 0.0

            blended = _blend_mouth_frame(
                sprites,
                level,
                blink_w,
                wink_weight=wink_w,
                brows_weight=brows_w,
            )
            raw_bytes = _apply_smooth_motion(
                blended,
                t_sec=idx / max(1, fps),
                duration=duration,
                energy=level,
                emotion=emotion,
                camera=camera,
                size=size,
                hand_overlay=hand_overlay,
            )
            proc.stdin.write(raw_bytes)
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
