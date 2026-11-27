# Voice Similarity, Rich Editor & Telegram Premium Userbot Handoff

## 1. Voice Similarity Improvements (`F5-TTS` & `Seed-VC`)
### Why 8s vs 82s happens
- **`F5-TTS`** (spoken TTS) uses zero-shot in-context cloning and works best on **7–12s** single-speaker clips; feeding 80s+ directly degrades attention alignment.
- **`Seed-VC`** (singing/rap covers) extracts a speaker embedding (`CAMPPlus`) and acoustic prompt from `--target`; using a **20–25s** clean multi-intonation reference (`voices/<profile>_singing.wav`) + `diffusion_steps >= 40` preserves vocal timbre and register significantly better than an 8s clip.

### Next-Level True Fine-Tuning (LoRA / Full FT)
For near-100% likeness on Ruslan (or any character with >=2–5 minutes of clean speech):
1. Collect 2–5 minutes of clean 24 kHz mono speech segments (4–12s each) with Whisper transcripts.
2. Fine-tune `Andreevai/f5-tts-russian-emotional` on the RTX 4060 8 GB (gradient checkpointing, batch size 2–4, fp16, ~30–60 min).
3. Point `voices/profiles.json` `"checkpoint"` for that profile to the fine-tuned weights.

## 2. Rich Editor (`[[rich-text]]`) & Telegram Premium Userbot Mode
### Implemented
- **`[[rich-text]]` directive** in `media_tools.py` + `trigger.py`: lets the model send rich-formatted Telegram HTML (`<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<blockquote>`, `<blockquote expandable>`, `<tg-spoiler>`, `<a href="...">`, `<tg-emoji emoji-id="...">`) when text formatting, code, or structured posts are requested instead of voice-only.
- **`premium_transport.py`** extended to support:
  - `--html-file` for sending Rich HTML messages from the authorized Telegram Premium account (`~/.local/share/rixed-joke-userbot/premium`),
  - `--video-note` / `--voice` for sending circular video notes and voice messages from the Premium account,
  - `--file` up to 4 GiB (Telegram Premium limit).

### Full Account Mode (Sending All Replies as User Account)
To switch the bot completely to the user's Telegram Premium account:
1. Enable `"use_userbot": "1"` in `settings` (can be wired as a `/commands` toggle).
2. Route `send_message`, `send_voice`, and `send_video_note` through `premium_transport.py` (or a persistent Telethon sidecar daemon) so every reply carries Telegram Premium capabilities (custom animated emoji `<tg-emoji>`, 4096+ char captions, 4 GB uploads, and native profile appearance).
