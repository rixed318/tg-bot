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

## 4. Multi-Outfit, Accessories & Avatar Studio Mini App + Marketplace
- **Ksyusha Gold-Standard Character:**
  - Outfits: `quality`, `street`, `cyber`, `cozy`, `beach` (swimwear + panama), `panama` (accessories), `buns` (hairstyle).
  - Each outfit has full lip-sync + blink + emotion set (`neutral_closed/half/open/blink`, `happy_open`, `laugh_open`, `sad_open`, `angry_open`, `shock_open`, `flirty_wink_open`).
- **Avatar Studio Mini App (`/arcade/studio`):**
  - Visual dress-up & customization Mini App: browse characters, outfits, hairstyles, accessories with live preview and 1-tap `Применить образ` (`POST /arcade/api/set_visual` -> updates `bot_settings`).
- **Avatar & Voice Marketplace (Магазин аватаров и голосов на заказ):**
  - Ready-to-use 2D character packs, custom outfit/accessory drops, and bespoke voice fine-tuning (`F5-TTS` from 8–10 min recording) + custom 2D persona generation on demand (Telegram Stars / TON Connect).
