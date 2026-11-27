# Singing-cover handoff

## Production flow

Audio requests containing `спой`, `спеть`, `перепой`, `перепеть`, `кавер` or
`под бит` are handled before the normal LLM reply in `worker.py`:

1. Telegram audio is downloaded and transcribed for persistent chat history.
2. Demucs `htdemucs --two-stems=vocals` produces `vocals.wav` and
   `no_vocals.wav`.
3. Seed-VC's F0-conditioned 44.1 kHz model converts the isolated vocal to the
   currently selected bot voice. Automatic F0 adjustment shifts the source
   register to the reference voice (important for male-source → female-profile
   covers) while retaining the melody, lyrics and timing. F5-TTS is deliberately
   not used for singing.
4. The converted vocal is mixed back over the original instrumental.
5. Both the mix and isolated converted vocal are split into 55-second chunks.
   The vocal chunk drives avatar mouth motion while the final video-note audio
   is the full mix. Telegram's 60-second video-note limit is therefore met with
   safety margin.

This produces three video notes for a 2:21 track. Each note replies to the
previous one so Telegram displays them as a clear sequence.

## Local runtime

- Seed-VC checkout: `/home/rus/dev/seed-vc`, commit
  `51383efd921027683c89e5348211d93ff12ac2a8`.
- Demucs checkout: `/home/rus/dev/demucs`, commit
  `e976d93ecc3865e5757426930257e200846a520a`.
- Isolated Python 3.10 environment: `/home/rus/dev/seed-vc/.venv`.
- Seed-VC main checkpoint, RMVPE and CAMPPlus weights live under
  `/home/rus/dev/seed-vc/checkpoints`.
- BigVGAN and `openai/whisper-small` are cached under
  `/home/rus/dev/seed-vc/checkpoints/hf_cache`.

The existing `faster-whisper` models cannot replace Seed-VC's Whisper model:
they are CTranslate2 transcription checkpoints, while Seed-VC directly reads
hidden states from the PyTorch Whisper encoder selected by its training
configuration.

Large model downloads must bypass the desktop HTTP proxy and Hugging Face Xet
on this machine. `singing_cover._run()` strips proxy variables and sets
`HF_HUB_DISABLE_XET=1` for child inference processes. The local Seed-VC
`inference.py` also accepts `SEED_VC_RMVPE_PATH` and
`SEED_VC_CAMPPLUS_PATH`; these two small environment hooks prevent redundant
downloads of weights already stored locally. The exact upstream-compatible
change is preserved in `deploy/seed-vc-local-paths.patch` and can be reapplied
with `git -C /home/rus/dev/seed-vc apply
/home/rus/dev/tg-bot/deploy/seed-vc-local-paths.patch`.

## GPU and failure behavior

The stages run sequentially on the RTX 4060 8 GB: Demucs exits before Seed-VC
starts, and the worker releases its cached faster-whisper model before singing
inference. The worker sends native Telegram upload actions throughout a long
cover build. Any stage failure raises normally, so the durable queue retries
the whole request instead of sending a partial or silent cover.

Configuration overrides:

- `TG_BOT_SINGING_ENGINE_ROOT`
- `TG_BOT_SINGING_ENGINE_PYTHON`
- `TG_BOT_SINGING_STEPS` (default `30`)
- `TG_BOT_SINGING_CHUNK_SECONDS` (default `55`)

## Verification

The preserved source used during development is
`/tmp/tg-bot-kishlak-source.m4a` (about 2:21). A short smoke run completed the
entire stem → conversion → mix → chunk path on the RTX 4060. Unit tests cover
intent detection, chunking and voice-reference resolution in
`tests/test_singing_cover.py`.
