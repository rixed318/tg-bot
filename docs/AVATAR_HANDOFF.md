# Avatar Animation Handoff & Roadmap

## Speech display modes (implemented)

`/commands` now exposes **Отображение речи** independently from the
voice/video-note format:

- `quality` — polished generated 2D pixel-art sprites. This is the default.
- `funny` — the original MediaPipe/OpenCV photo sprites kept in sibling
  `<character>_funny` directories.

The setting is stored as `speech_display` in the shared queue database and is
included in every claimed task. The local worker passes it to
`resolve_avatar_dir()`. For funny mode the resolver first tries
`voices/avatars/<persona>_funny`, then `<voice>_funny`; if a funny variant does
not exist it safely falls back to the normal profile avatar.

The generated 4x7 sheets use this fixed row layout:

1. `neutral`, 2. `happy`, 3. `angry`, 4. `laugh`, 5. `shock`, 6. `sad`;
each row is `closed`, `half`, `open`, `blink`. Row 7 contains
`neutral_wink`, `flirty_wink_open`, `brows_up_open`, and one unused cell.
Run `scripts/split_avatar_sheet.py SHEET OUTPUT_DIR` to produce the renderer's
27 files at 384x384.

## Identity fidelity requirement

The current generated Karina and Katya quality sprites are temporary: their
general hair, clothing and palette are usable, but their faces are not close
enough to the source photographs.

All future quality-avatar work must preserve the person's identity in detail,
not merely produce a similar generic 2D character. Use the original photograph
as the primary identity reference and the Anya sprites only as a style/layout
reference. Preserve the characteristic face shape, eye shape and spacing,
eyebrows, nose, lips, cheek structure, hairline, hairstyle, beauty marks and
other distinguishing features. Expression variants may change only the eyes,
eyebrows and mouth; they must not redesign the face.

Before replacing a quality set:

1. Generate and review the neutral portrait against the source photograph.
2. Do not proceed to the full expression sheet until the neutral identity is
   recognizably the same person.
3. Keep one canonical neutral render and derive every expression from it with
   strong identity preservation, instead of independently redesigning cells.
4. Visually review at least neutral, happy, angry, blink and open-mouth frames
   after splitting, plus one rendered Telegram video-note frame.

Karina and Katya remain explicitly queued for a high-fidelity redraw under
these rules. Artem and Ksyusha should also be checked against this bar during
the next avatar pass.

## Current State (Option 1 — Smooth 2D Sprite Engine)
Implemented in `src/tg_bot/avatar_video.py`:
- Temporal attack/release smoothing on audio RMS envelope (`compute_smoothed_envelope`) to eliminate 1-frame mouth jitter.
- Continuous crossfade blending between `closed` ↔ `half` ↔ `open` (and smooth 5-frame blink transitions) instead of hard frame cuts.
- Smooth continuous head breathing, sway, and speech-driven nod using `1.5%+` overscan affine transforms (no jerky `np.roll` jumps).

## Neural / Viseme optimization roadmap (RTX 4060 8 GB)

### 1. Per-Character Optimization Goals
- **Phoneme / Viseme Alignment (Произношение букв):**
  - Instead of RMS-only mouth openness, extract forced-aligned phonemes or acoustic formants/MFCCs from the synthesized `.wav` (or reuse `F5-TTS` / `RUAccent` text+phoneme stream).
  - Map Russian phonemes to viseme groups (`bilabial: п/б/м`, `labiodental: ф/в`, `rounded: о/у`, `wide: а/я`, `spread: и/е`, `neutral`) for accurate lip shapes.
- **Per-Avatar Facial Expression & Calibration:**
  - Store per-character avatar config in `voices/avatars/<name>/avatar.json`:
    - reference face crop / landmark bounding box,
    - emotion intensity multipliers (`laugh`, `angry`, `sad`, `shock`, `happy`),
    - idle head pose / blink cadence / smile baseline.
- **Future neural mode in `/commands`:**
  - Keep `avatar_enabled` for voice-only vs video-note and `speech_display` for
    quality vs funny 2D. Add a separate neural renderer value only when
    LivePortrait / MuseTalk is production-ready.

### 2. GPU Memory Strategy (RTX 4060 8 GB VRAM)
- `F5-TTS` holds ~1.5–2.5 GB VRAM during synthesis.
- To avoid `CUDA out of memory` on 8 GB VRAM:
  1. Synthesize `.wav` via `F5-TTS`, then move/offload TTS model to CPU or call `torch.cuda.empty_cache()`.
  2. Run `LivePortrait` / `MuseTalk` lip-sync inference on GPU (~10–25s per 15s clip).
  3. Cache pre-extracted source face keypoints/latents per avatar (`voices/avatars/<name>/cache.pt`) so each reply skips source-image encoding.
