# Next-Gen Bot Roadmap & Handoff

## 1. Voice Fine-Tuning (2–10 min Ruslan Recording -> `F5-TTS` LoRA / FT)
Zero-shot cloning (`voices/ruslan.wav`) captures general timbre, while **true fine-tuning** on 2–10 minutes of clean speech makes cadence, breaths, and micro-intonations nearly indistinguishable from the original speaker.

### Pipeline Plan
1. User sends a 2–10 minute voice recording (or `.ogg`/`.wav`/`.m4a`).
2. `scripts/prepare_voice_dataset.py`:
   - Convert to 24 kHz mono WAV, run `highpass=f=80` + `loudnorm`.
   - Split on VAD silences into 4–11s chunks (`>20` segments).
   - Transcribe every chunk with `faster-whisper large-v3-turbo` (`language="ru"`).
   - Write metadata CSV (`audio_path|transcript`).
3. Fine-tune `Andreevai/f5-tts-russian-emotional` (LoRA / fp16 with gradient checkpointing on RTX 4060 8 GB).
4. Register `"checkpoint"` in `voices/profiles.json` so `voice_tts.py` loads the fine-tuned adapter for that character.

## 2. Telegram Bot vs Userbot (Premium) & 4KB Mini-Apps
- **Bot API (`🤖 Бот`, default):**
  - Required for **4KB Rich HTML messages** (`<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<blockquote>`, `<blockquote expandable>`, `<tg-spoiler>`, `<a href="...">`).
  - Required for **Telegram Mini Apps (`WebAppInfo`)** embedded directly inside bot messages / inline buttons (interactive HTML/JS micro-apps launched right inside chat!).
  - Required for inline `/commands` keyboards and `sendMessageDraft` streaming.
- **Telegram Premium Userbot (`⭐ Мой Premium` via `rixed-joke-userbot` / Telethon):**
  - Sends as the user's own Telegram Premium account (supports **4 GiB file uploads**, native Premium custom emoji `<tg-emoji>`, and appearing as the user's personal account).
  - *Note:* Telegram Bot API Mini Apps and bot inline panels cannot be sent by a regular user account — keep `🤖 Бот` as default and use `Premium` transport specifically for >50 MB uploads or personal-account replies.

## 3. Autonomous Format Switching (`[[rich-text]]`) & Camera Motion (`[[camera:...]]`)
- **Implemented:** The LLM can autonomously output `[[rich-text]]` even when circular video mode is active whenever the user asks for detailed breakdowns, guides, code, tables (`<pre>`), or expandable blocks (`<blockquote expandable>`).
- **Implemented:** `[[camera:pan_up|pan_down|pan_left|pan_right|zoom_in|zoom_out|shake|auto]]` drives smooth eased camera pans/zooms inside `avatar_video.py`.

## 4. Full Body / Head / Hands / Outfit Animation & Visual Selector
- **Implemented in `/commands`:** Removed `Пресет` (voice and behavior are always independent) and `Обновить`; added `👗 Визуал` selector (`Обычный (2D арт)` / `Ржачный (фото)` + extensible per-character outfit folders `voices/avatars/<char>/<outfit>/`).
- **Next stage:**
  - Multi-layer character rigs (torso + head + hands + outfit PNG layers) or `LivePortrait` / `MuseTalk` GPU rendering on RTX 4060.
  - Per-character outfit packs (`casual`, `street`, `cyber`, `suit`) selectable with live preview + auto-cleanup in `/commands`.
