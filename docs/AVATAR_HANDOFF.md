# Avatar Animation Handoff & Roadmap (Option 2)

## Current State (Option 1 — Smooth 2D Sprite Engine)
Implemented in `src/tg_bot/avatar_video.py`:
- Temporal attack/release smoothing on audio RMS envelope (`compute_smoothed_envelope`) to eliminate 1-frame mouth jitter.
- Continuous crossfade blending between `closed` ↔ `half` ↔ `open` (and smooth 5-frame blink transitions) instead of hard frame cuts.
- Smooth continuous head breathing, sway, and speech-driven nod using `1.5%+` overscan affine transforms (no jerky `np.roll` jumps).

## Option 2 Roadmap — Per-Avatar Neural / Viseme Optimization (RTX 4060 8 GB)

### 1. Per-Character Optimization Goals
- **Phoneme / Viseme Alignment (Произношение букв):**
  - Instead of RMS-only mouth openness, extract forced-aligned phonemes or acoustic formants/MFCCs from the synthesized `.wav` (or reuse `F5-TTS` / `RUAccent` text+phoneme stream).
  - Map Russian phonemes to viseme groups (`bilabial: п/б/м`, `labiodental: ф/в`, `rounded: о/у`, `wide: а/я`, `spread: и/е`, `neutral`) for accurate lip shapes.
- **Per-Avatar Facial Expression & Calibration:**
  - Store per-character avatar config in `voices/avatars/<name>/avatar.json`:
    - reference face crop / landmark bounding box,
    - emotion intensity multipliers (`laugh`, `angry`, `sad`, `shock`, `happy`),
    - idle head pose / blink cadence / smile baseline.
- **Format Toggle in `/commands`:**
  - Extend `avatar_enabled` in `gateway.py` / `queue_db.py` into `avatar_mode`: `off` (voice-only), `2d` (fast smooth 2D), `neural` (LivePortrait / MuseTalk).

### 2. GPU Memory Strategy (RTX 4060 8 GB VRAM)
- `F5-TTS` holds ~1.5–2.5 GB VRAM during synthesis.
- To avoid `CUDA out of memory` on 8 GB VRAM:
  1. Synthesize `.wav` via `F5-TTS`, then move/offload TTS model to CPU or call `torch.cuda.empty_cache()`.
  2. Run `LivePortrait` / `MuseTalk` lip-sync inference on GPU (~10–25s per 15s clip).
  3. Cache pre-extracted source face keypoints/latents per avatar (`voices/avatars/<name>/cache.pt`) so each reply skips source-image encoding.
