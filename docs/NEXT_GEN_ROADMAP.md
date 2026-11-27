# Next-Gen Bot Roadmap & Handoff

## 1. Voice Fine-Tuning (8–10 min Ruslan Recording -> `F5-TTS` LoRA / FT)
User confirmed recording **8–10 minutes** of varied Russian speech (calm, emotional, laugh, surprise, fast/intense, pauses).
### Pipeline Plan
1. Place recording in `/home/rus/Downloads/` (`.ogg` / `.wav` / `.m4a`).
2. `scripts/prepare_voice_dataset.py`:
   - Convert to 24 kHz mono WAV, `highpass=f=80`, `loudnorm=I=-16`.
   - Split on VAD silences into 4–11s segments.
   - Transcribe with `faster-whisper large-v3-turbo` (`language="ru"`).
3. Fine-tune `Andreevai/f5-tts-russian-emotional` on RTX 4060 8 GB (fp16, gradient checkpointing).
4. Register `"checkpoint"` in `voices/profiles.json`.

## 2. Video-to-Sticker & Custom Emoji (with Time Range)
Telegram limits animated/video stickers and custom emojis to **<= 3.0 seconds**, `.webm` VP9 without audio (`512×512` for stickers, `100×100` for custom emoji, `<= 256 KB`).
- **Implemented (`media_tools.py`):**
  - `make_clean_emoji_webp` (`100×100` `.webp` for photo -> emoji).
  - `make_video_sticker_or_emoji_webm` (`512×512` or `100×100` `.webm` VP9, `<= 3s`, supports `--start` / `--end` time range).
  - Directives: `[[make-emoji]]`, `[[make-sticker:00:05-00:08]]`, `[[make-emoji:00:05-00:08]]`.
- **Next:** If user sends a >4s video asking for a sticker/emoji without specifying timestamps, the bot asks which 3-second range (`например 00:05-00:08`) to cut.

## 3. In-Chat Games & Leaderboard (`src/tg_bot/games.py`)
- Persistent leaderboard by `user_id`, `username`, `display_name`, `game`, `score`.
- **Games:**
  - **Змейка / 2048** (playable 4KB Telegram WebApp / inline game posting scores back to bot leaderboard).
  - **Мафия / Бункер (компанейская)** — multi-player group lobby via inline buttons (`Присоединиться`, `Начать игру`, ночные роли в ЛС, дневное голосование в группе).
  - **Быстрые чат-дуэли** (`/commands -> 🎮 Игры`: бросок кубика / дуэль / таблица рекордов).

## 4. Multi-Outfit & Body/Hands/Torso Character System
- Per-character visual outfits under `voices/avatars/<char>_<outfit>/` (e.g. `anya_street`, `ruslan_street`, `anya_cyber`, etc.) selectable in `/commands -> 👗 Визуал` with live preview & auto-cleanup.
- Full body/torso/head/hands layered sprite rig & `LivePortrait` GPU upgrade path.
