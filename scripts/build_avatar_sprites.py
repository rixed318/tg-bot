#!/usr/bin/env python3
"""Generate a full 27-sprite 2D talking-avatar expression pack from any portrait photo."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
import numpy as np

AVATAR_SIZE = 384

# MediaPipe 468/478 landmark indices
LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]
LEFT_EYE_UPPER = [159, 158, 160]
LEFT_EYE_LOWER = [145, 144, 153]
RIGHT_EYE_UPPER = [386, 387, 385]
RIGHT_EYE_LOWER = [374, 380, 373]
LEFT_BROW = [70, 63, 105, 66, 107]
RIGHT_BROW = [336, 296, 334, 293, 300]
UPPER_LIP_CENTER = [13, 12, 0, 37, 267]
LOWER_LIP_CENTER = [14, 15, 17, 84, 314]
MOUTH_CORNER_L = 61
MOUTH_CORNER_R = 291
INNER_MOUTH_POLY = [78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308, 324, 318, 402, 317, 14, 87, 178, 88, 95]


def _detect_landmarks(img_rgb: np.ndarray, model_path: Path) -> np.ndarray:
    base_options = mp_python.BaseOptions(model_asset_path=str(model_path))
    options = mp_vision.FaceLandmarkerOptions(
        base_options=base_options,
        num_faces=1,
        min_face_detection_confidence=0.35,
        min_face_presence_confidence=0.35,
    )
    with mp_vision.FaceLandmarker.create_from_options(options) as landmarker:
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
        res = landmarker.detect(mp_img)
        if not res.face_landmarks:
            raise RuntimeError("No face detected in image")
        h, w = img_rgb.shape[:2]
        pts = np.array([[lm.x * w, lm.y * h] for lm in res.face_landmarks[0]], dtype=np.float32)
        return pts


def _crop_portrait_square(img_bgr: np.ndarray, model_path: Path, size: int = AVATAR_SIZE) -> np.ndarray:
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    pts = _detect_landmarks(img_rgb, model_path)
    x_min, y_min = pts.min(axis=0)
    x_max, y_max = pts.max(axis=0)
    face_w = x_max - x_min
    face_h = y_max - y_min
    cx = 0.5 * (x_min + x_max)
    cy = y_min + 0.48 * face_h
    box = max(face_w * 2.15, face_h * 2.05)

    h, w = img_bgr.shape[:2]
    box = min(box, float(min(w, h)) * 1.15)
    x1 = int(round(cx - box * 0.5))
    y1 = int(round(cy - box * 0.46))
    x2 = int(round(x1 + box))
    y2 = int(round(y1 + box))

    # Clamp inside image bounds while preserving square aspect
    if x1 < 0:
        x2 -= x1
        x1 = 0
    if y1 < 0:
        y2 -= y1
        y1 = 0
    if x2 > w:
        shift = x2 - w
        x1 = max(0, x1 - shift)
        x2 = w
    if y2 > h:
        shift = y2 - h
        y1 = max(0, y1 - shift)
        y2 = h

    side = min(x2 - x1, y2 - y1)
    crop = img_bgr[y1 : y1 + side, x1 : x1 + side]
    return cv2.resize(crop, (size, size), interpolation=cv2.INTER_LANCZOS4)


def _add_warp(
    dx_map: np.ndarray,
    dy_map: np.ndarray,
    gx: np.ndarray,
    gy: np.ndarray,
    center: tuple[float, float],
    shift: tuple[float, float],
    radius: float,
) -> None:
    cx, cy = center
    sx, sy = shift
    dist2 = (gx - cx) ** 2 + (gy - cy) ** 2
    w = np.exp(-dist2 / (2.0 * radius * radius)).astype(np.float32)
    dx_map += sx * w
    dy_map += sy * w


def _render_variant(
    base_bgr: np.ndarray,
    pts: np.ndarray,
    *,
    mouth_open: float = 0.0,
    mouth_wide: float = 0.0,
    smile: float = 0.0,
    brow_lift: float = 0.0,
    brow_furrow: float = 0.0,
    blink_l: float = 0.0,
    blink_r: float = 0.0,
    shock_eyes: float = 0.0,
) -> np.ndarray:
    h, w = base_bgr.shape[:2]
    gy, gx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx_map = np.zeros((h, w), dtype=np.float32)
    dy_map = np.zeros((h, w), dtype=np.float32)

    face_scale = float(np.linalg.norm(pts[MOUTH_CORNER_R] - pts[MOUTH_CORNER_L])) / 58.0
    s = max(0.7, min(1.5, face_scale))

    # Mouth opening (lower lip + jaw drop, slight upper lip lift)
    if abs(mouth_open) > 0.01:
        lower_c = pts[17]
        upper_c = pts[0]
        _add_warp(dx_map, dy_map, gx, gy, (lower_c[0], lower_c[1]), (0.0, 14.0 * mouth_open * s), 22.0 * s)
        _add_warp(dx_map, dy_map, gx, gy, (upper_c[0], upper_c[1]), (0.0, -2.5 * mouth_open * s), 14.0 * s)

    # Mouth width (narrow for 'O' shock, wider for laugh)
    if abs(mouth_wide) > 0.01:
        _add_warp(dx_map, dy_map, gx, gy, tuple(pts[MOUTH_CORNER_L]), (-7.0 * mouth_wide * s, 0.0), 14.0 * s)
        _add_warp(dx_map, dy_map, gx, gy, tuple(pts[MOUTH_CORNER_R]), (7.0 * mouth_wide * s, 0.0), 14.0 * s)

    # Smile / frown corners
    if abs(smile) > 0.01:
        _add_warp(dx_map, dy_map, gx, gy, tuple(pts[MOUTH_CORNER_L]), (-2.5 * smile * s, -6.0 * smile * s), 15.0 * s)
        _add_warp(dx_map, dy_map, gx, gy, tuple(pts[MOUTH_CORNER_R]), (2.5 * smile * s, -6.0 * smile * s), 15.0 * s)

    # Eyebrow raise / sad inner lift
    if abs(brow_lift) > 0.01:
        for idx in LEFT_BROW[1:4] + RIGHT_BROW[1:4]:
            _add_warp(dx_map, dy_map, gx, gy, tuple(pts[idx]), (0.0, -5.5 * brow_lift * s), 16.0 * s)

    # Angry brow furrow
    if abs(brow_furrow) > 0.01:
        _add_warp(dx_map, dy_map, gx, gy, tuple(pts[107]), (4.0 * brow_furrow * s, 5.5 * brow_furrow * s), 15.0 * s)
        _add_warp(dx_map, dy_map, gx, gy, tuple(pts[336]), (-4.0 * brow_furrow * s, 5.5 * brow_furrow * s), 15.0 * s)

    # Shocked wide eyes
    if shock_eyes > 0.05:
        for eye_up, eye_lo in ((LEFT_EYE_UPPER, LEFT_EYE_LOWER), (RIGHT_EYE_UPPER, RIGHT_EYE_LOWER)):
            up_pt = pts[eye_up].mean(axis=0)
            lo_pt = pts[eye_lo].mean(axis=0)
            _add_warp(dx_map, dy_map, gx, gy, (up_pt[0], up_pt[1]), (0.0, -3.5 * shock_eyes * s), 7.5 * s)
            _add_warp(dx_map, dy_map, gx, gy, (lo_pt[0], lo_pt[1]), (0.0, 2.0 * shock_eyes * s), 7.5 * s)

    # Inverse remap: destination pixel pulls from source - displacement
    map_x = np.clip(gx - dx_map, 0, w - 1).astype(np.float32)
    map_y = np.clip(gy - dy_map, 0, h - 1).astype(np.float32)
    warped = cv2.remap(base_bgr, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)

    # Smooth skin-sampled eyelid closure for blink/wink (keeps eyebrows 100% untouched)
    for eye_idx, eye_up, eye_lo, amt in (
        (LEFT_EYE, LEFT_EYE_UPPER, LEFT_EYE_LOWER, blink_l),
        (RIGHT_EYE, RIGHT_EYE_UPPER, RIGHT_EYE_LOWER, blink_r),
    ):
        if amt > 0.08:
            poly = np.round(pts[eye_idx]).astype(np.int32)
            ex, ey, ew, eh = cv2.boundingRect(poly)
            sample_y = max(0, ey - max(3, int(round(4 * s))))
            sample_patch = warped[sample_y : sample_y + max(2, int(round(3 * s))), ex : ex + ew]
            lid_color = (
                np.median(sample_patch.reshape(-1, 3), axis=0).astype(np.float32)
                if sample_patch.size > 0
                else np.array([150.0, 165.0, 195.0], dtype=np.float32)
            )
            eye_mask = np.zeros((h, w), dtype=np.float32)
            cv2.ellipse(
                eye_mask,
                (int(round(ex + ew * 0.5)), int(round(ey + eh * 0.5))),
                (max(4, int(round(ew * 0.58))), max(3, int(round(eh * 0.72)))),
                0,
                0,
                360,
                1.0,
                -1,
            )
            eye_mask = cv2.GaussianBlur(eye_mask, (5, 5), 1.4)
            alpha_e = (eye_mask * min(1.0, amt))[..., None]
            warped = np.clip(warped.astype(np.float32) * (1.0 - alpha_e) + lid_color * alpha_e, 0, 255).astype(np.uint8)

    # Paint clean smooth elliptical dark mouth interior (never self-intersecting)
    if mouth_open > 0.22:
        up_m = pts[13]
        lo_m = pts[14]
        mc = 0.5 * (up_m + lo_m)
        mc[1] += 4.5 * mouth_open * s
        m_width = float(np.linalg.norm(pts[MOUTH_CORNER_R] - pts[MOUTH_CORNER_L]))
        rx = max(4, int(round(m_width * (0.31 + 0.08 * mouth_wide))))
        ry = max(2, int(round((5.8 * mouth_open + 1.5) * s)))
        mouth_angle = float(np.degrees(np.arctan2(
            pts[MOUTH_CORNER_R][1] - pts[MOUTH_CORNER_L][1],
            pts[MOUTH_CORNER_R][0] - pts[MOUTH_CORNER_L][0],
        )))
        mask = np.zeros((h, w), dtype=np.float32)
        cv2.ellipse(mask, (int(round(mc[0])), int(round(mc[1]))), (rx, ry), mouth_angle, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (7, 7), 1.6)
        cavity_color = np.array([24, 18, 42], dtype=np.float32)  # BGR deep maroon-dark
        alpha = (mask * min(0.9, 0.42 + 0.48 * mouth_open))[..., None]
        warped = np.clip(warped.astype(np.float32) * (1.0 - alpha) + cavity_color * alpha, 0, 255).astype(np.uint8)

    # Subtle upper-eyelid lash line on closed eyes for crisp blink look
    for eye_up, eye_lo, amt in ((LEFT_EYE_UPPER, LEFT_EYE_LOWER, blink_l), (RIGHT_EYE_UPPER, RIGHT_EYE_LOWER, blink_r)):
        if amt > 0.65:
            p1 = pts[LEFT_EYE[0] if eye_up is LEFT_EYE_UPPER else RIGHT_EYE[0]]
            p2 = pts[LEFT_EYE[3] if eye_up is LEFT_EYE_UPPER else RIGHT_EYE[3]]
            mid = pts[eye_lo].mean(axis=0)
            curve = np.array([p1, [mid[0], mid[1] + 1.5 * s], p2], dtype=np.int32)
            overlay = warped.copy()
            cv2.polylines(overlay, [curve], False, (36, 28, 32), max(1, int(round(1.8 * s))), cv2.LINE_AA)
            warped = cv2.addWeighted(overlay, 0.55 * amt, warped, 1.0 - 0.55 * amt, 0)

    return warped


def build_pack(photo_path: Path, out_dir: Path, model_path: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    img_bgr = cv2.imread(str(photo_path))
    if img_bgr is None:
        raise RuntimeError(f"Could not read {photo_path}")
    cropped = _crop_portrait_square(img_bgr, model_path, AVATAR_SIZE)
    cropped_rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
    pts = _detect_landmarks(cropped_rgb, model_path)

    emotion_specs = {
        "neutral": dict(smile=0.0, brow_lift=0.0, brow_furrow=0.0, blink_l=0.0, blink_r=0.0, shock_eyes=0.0),
        "happy": dict(smile=0.75, brow_lift=0.25, brow_furrow=0.0, blink_l=0.0, blink_r=0.0, shock_eyes=0.0),
        "laugh": dict(smile=1.05, brow_lift=0.35, brow_furrow=0.0, blink_l=0.35, blink_r=0.35, shock_eyes=0.0),
        "angry": dict(smile=-0.55, brow_lift=0.0, brow_furrow=1.0, blink_l=0.0, blink_r=0.0, shock_eyes=0.0),
        "shock": dict(smile=-0.1, brow_lift=1.0, brow_furrow=0.0, blink_l=0.0, blink_r=0.0, shock_eyes=1.0),
        "sad": dict(smile=-0.7, brow_lift=0.45, brow_furrow=0.15, blink_l=0.15, blink_r=0.15, shock_eyes=0.0),
    }

    for emo, base_kw in emotion_specs.items():
        wide = -0.55 if emo == "shock" else (0.35 if emo in ("happy", "laugh") else 0.0)
        for state, m_open in (("closed", 0.0), ("half", 0.48), ("open", 1.0)):
            kw = dict(base_kw)
            frame = _render_variant(cropped, pts, mouth_open=m_open, mouth_wide=wide * m_open, **kw)
            cv2.imwrite(str(out_dir / f"{emo}_{state}.png"), frame)
        blink_kw = dict(base_kw, blink_l=1.0, blink_r=1.0)
        blink_frame = _render_variant(cropped, pts, mouth_open=0.0, **blink_kw)
        cv2.imwrite(str(out_dir / f"{emo}_blink.png"), blink_frame)

    # Extra expressive presets: wink, talking wink, brows up
    cv2.imwrite(
        str(out_dir / "neutral_wink.png"),
        _render_variant(cropped, pts, mouth_open=0.0, smile=0.55, blink_l=1.0, blink_r=0.0),
    )
    cv2.imwrite(
        str(out_dir / "flirty_wink_open.png"),
        _render_variant(cropped, pts, mouth_open=0.9, mouth_wide=0.25, smile=0.7, blink_l=1.0, blink_r=0.0),
    )
    cv2.imwrite(
        str(out_dir / "brows_up_open.png"),
        _render_variant(cropped, pts, mouth_open=0.95, brow_lift=1.0, smile=0.2),
    )
    print(f"Built 27 sprites in {out_dir}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("photo", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--model", type=Path, default=Path(".cache/face_landmarker.task"))
    args = parser.parse_args()
    build_pack(args.photo, args.out_dir, args.model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
