"""Russian speech normalization, zero-shot voice cloning (F5-TTS), and Edge-TTS fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import subprocess
import threading
import urllib.request
from typing import Any

from tg_bot.config import FFMPEG_BIN, FFPROBE_BIN, VOICE_PROFILES_FILE, VOICES_DIR
from tg_bot.transcribe import transcribe_audio

LOGGER = logging.getLogger("tg_bot.voice")

F5_DEFAULT_REPO = "Andreevai/f5-tts-russian-emotional"
F5_DEFAULT_WEIGHTS = "model.safetensors"
F5_DEFAULT_VOCAB = "vocab.txt"

# Fallback open-source sample voice (downloads automatically if no local voice exists yet)
DEFAULT_EDGE_SEED_TEXT = (
    "Привет! Я на связи в голосовом режиме. Если нужно разобрать блендер, рендер или просто поболтать — записывайте, всё услышу и отвечу нормально."
)

# Common English/3D terms normalized into Russian phonetics for crisp Russian TTS
PHONETIC_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bblender\b", re.I), "блэндер"),
    (re.compile(r"\bcycles\b", re.I), "сайклз"),
    (re.compile(r"\beevee\b", re.I), "иви"),
    (re.compile(r"\brender\b", re.I), "рэндер"),
    (re.compile(r"\bshader\b", re.I), "шейдер"),
    (re.compile(r"\bshaders\b", re.I), "шейдеры"),
    (re.compile(r"\bgeometry\s*nodes\b", re.I), "геометри нодс"),
    (re.compile(r"\bmesh\b", re.I), "мэш"),
    (re.compile(r"\bsubsurf\b", re.I), "сабсёрф"),
    (re.compile(r"\bboolean\b", re.I), "булеан"),
    (re.compile(r"\bbevel\b", re.I), "бэвел"),
    (re.compile(r"\bretopo(?:logy)?\b", re.I), "ретопология"),
    (re.compile(r"\bsculpt(?:ing)?\b", re.I), "скульптинг"),
    (re.compile(r"\brigging\b", re.I), "риггинг"),
    (re.compile(r"\bvram\b", re.I), "вирам"),
    (re.compile(r"\bgpu\b", re.I), "джи пи ю"),
    (re.compile(r"\bcpu\b", re.I), "си пи ю"),
    (re.compile(r"\bfps\b", re.I), "эф пэ эс"),
    (re.compile(r"\buv\b", re.I), "ю ви"),
    (re.compile(r"\bhdri\b", re.I), "эйч ди ар ай"),
    (re.compile(r"\bpbr\b", re.I), "пи би ар"),
    (re.compile(r"\bfbx\b", re.I), "эф бэ икс"),
    (re.compile(r"\bgltf\b", re.I), "джи эл ти эф"),
    (re.compile(r"\bglb\b", re.I), "джи эл би"),
    (re.compile(r"\bobj\b", re.I), "о бэ джей"),
    (re.compile(r"\bstl\b", re.I), "эс тэ эль"),
    (re.compile(r"\b3d\b", re.I), "три дэ"),
    (re.compile(r"\b2d\b", re.I), "два дэ"),
    (re.compile(r"\bai\b", re.I), "ИИ"),
    (re.compile(r"\btelegram\b", re.I), "телеграм"),
    (re.compile(r"\bpython\b", re.I), "пайтон"),
    (re.compile(r"\bdiscord\b", re.I), "дискорд"),
    (re.compile(r"\byoutube\b", re.I), "ютуб"),
    (re.compile(r"\bgoogle\s*drive\b", re.I), "гугл диск"),
    (re.compile(r"\bgithub\b", re.I), "гитхаб"),
    (re.compile(r"\bok\b", re.I), "окей"),
]

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "\U0000FE00-\U0000FE0F"
    "\U0001F1E6-\U0001F1FF"
    "]+",
    flags=re.UNICODE,
)

_MODEL_LOCK = threading.Lock()
_F5_RUNTIME: dict[str, Any] | None = None
_RUACCENT_MODEL: Any = None


@dataclass(frozen=True)
class VoiceResult:
    ogg_path: Path
    duration_seconds: int
    backend: str
    voice_name: str
    spoken_text: str


def normalize_text_for_speech(text: str) -> str:
    """Clean markdown, emojis, URLs, numbers, and common 3D terms for natural Russian TTS."""
    cleaned = re.sub(r"\[\[[^\]]+\]\]", " ", text)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"```[\s\S]*?```", " ", cleaned)
    cleaned = re.sub(r"[`*_~#>|]", " ", cleaned)
    cleaned = re.sub(r"^\s*[-•]\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = EMOJI_PATTERN.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    for pattern, replacement in PHONETIC_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)

    # Convert standalone integers (up to 7 digits) to Russian words when num2words is installed
    try:
        from num2words import num2words

        def _num_repl(match: re.Match[str]) -> str:
            val = int(match.group(0))
            if val > 9_999_999:
                return match.group(0)
            return num2words(val, lang="ru")

        cleaned = re.sub(r"\b\d+\b", _num_repl, cleaned)
    except Exception:
        pass

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "Принял, всё готово."


def _apply_ruaccent(text: str) -> str:
    """Annotate Russian stress marks using '+' for F5-TTS_RUSSIAN when ruaccent is available."""
    global _RUACCENT_MODEL
    if "+" in text:
        return text
    try:
        if _RUACCENT_MODEL is None:
            from ruaccent import RUAccent

            accentizer = RUAccent()
            accentizer.load(omograph_model_size="turbo3.1", use_dictionary=True, tiny_mode=False)
            _RUACCENT_MODEL = accentizer
        return str(_RUACCENT_MODEL.process_all(text))
    except Exception as exc:
        LOGGER.warning("RUAccent skipped (%s); synthesizing without auto-stress", exc)
        return text


def probe_duration_seconds(audio_path: Path) -> int:
    """Measure audio duration in rounded integer seconds via ffprobe."""
    try:
        res = subprocess.run(
            [
                FFPROBE_BIN,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return max(1, int(round(float(res.stdout.strip()))))
    except Exception:
        return 1


def convert_to_telegram_ogg(
    input_audio: Path, output_ogg: Path, *, audio_fx: str | None = None
) -> Path:
    """Encode audio as 48kHz mono OGG/Opus for native Telegram voice message playback."""
    output_ogg.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG_BIN, "-y", "-i", str(input_audio)]
    if audio_fx:
        cmd.extend(["-af", audio_fx])
    cmd.extend(
        [
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "libopus",
            "-b:a",
            "48k",
            "-application",
            "voip",
            str(output_ogg),
        ]
    )
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return output_ogg


def _default_profiles() -> dict[str, Any]:
    return {
        "active": "default",
        "edge_voice": "ru-RU-DmitryNeural",
        "profiles": {
            "default": {
                "label": "Стандартный голос (авто)",
                "audio": "default.wav",
                "transcript_file": "default.txt",
            }
        },
    }


def load_voice_profiles() -> dict[str, Any]:
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    if not VOICE_PROFILES_FILE.is_file():
        data = _default_profiles()
        VOICE_PROFILES_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return data
    try:
        data = json.loads(VOICE_PROFILES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "profiles" in data:
            return data
    except Exception:
        pass
    return _default_profiles()


def save_voice_profiles(data: dict[str, Any]) -> None:
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    VOICE_PROFILES_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def ensure_default_reference_voice() -> tuple[Path, str]:
    """Ensure voices/default.wav + default.txt exist (synthesizes a crisp seed via Edge-TTS if needed)."""
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    wav_path = VOICES_DIR / "default.wav"
    txt_path = VOICES_DIR / "default.txt"
    if wav_path.is_file() and wav_path.stat().st_size > 1000:
        if txt_path.is_file() and txt_path.read_text(encoding="utf-8").strip():
            return wav_path, txt_path.read_text(encoding="utf-8").strip()
        transcript = transcribe_audio(wav_path, language="ru") or DEFAULT_EDGE_SEED_TEXT
        txt_path.write_text(transcript, encoding="utf-8")
        return wav_path, transcript

    # Bootstrap clean 24kHz mono WAV seed using Edge-TTS if no default.wav was placed manually.
    mp3_tmp = VOICES_DIR / "_seed_tmp.mp3"
    try:
        _synthesize_edge_tts_raw(DEFAULT_EDGE_SEED_TEXT, mp3_tmp, voice="ru-RU-DmitryNeural")
        subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-i",
                str(mp3_tmp),
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                str(wav_path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        txt_path.write_text(DEFAULT_EDGE_SEED_TEXT, encoding="utf-8")
    finally:
        if mp3_tmp.is_file():
            mp3_tmp.unlink()
    return wav_path, DEFAULT_EDGE_SEED_TEXT


def resolve_voice_profile_name(query: str, profiles_data: dict[str, Any] | None = None) -> str | None:
    """Resolve a profile ID or Russian alias (case-insensitive) to canonical profile key."""
    data = profiles_data or load_voice_profiles()
    profiles = data.get("profiles") or {}
    q = query.strip().lower()
    if not q:
        return None
    if q in profiles:
        return q
    for key, entry in profiles.items():
        label = str(entry.get("label") or "").lower()
        aliases = [str(a).lower() for a in (entry.get("aliases") or [])]
        if q == label or q in aliases or (len(q) >= 3 and any(q in a for a in aliases)):
            return key
    return None


def get_active_voice_reference() -> tuple[str, Path, str, dict[str, Any]]:
    """Return (profile_name, reference_wav_path, reference_transcript, profile_meta)."""
    profiles = load_voice_profiles()
    active_name = str(profiles.get("active") or "default")
    entry = (profiles.get("profiles") or {}).get(active_name)
    if entry:
        audio_path = VOICES_DIR / str(entry["audio"])
        txt_path = VOICES_DIR / str(entry.get("transcript_file", ""))
        if audio_path.is_file() and audio_path.stat().st_size > 1000:
            transcript = (
                txt_path.read_text(encoding="utf-8").strip()
                if txt_path.is_file()
                else ""
            )
            if not transcript:
                transcript = transcribe_audio(audio_path, language="ru") or "Привет, я на связи."
                if txt_path:
                    txt_path.write_text(transcript, encoding="utf-8")
            return active_name, audio_path, transcript, entry

    wav_path, transcript = ensure_default_reference_voice()
    return "default", wav_path, transcript, {}


def set_custom_voice_from_audio(
    source_audio: Path,
    *,
    profile_name: str = "custom",
    transcript_override: str | None = None,
) -> tuple[Path, str]:
    """Normalize a user-recorded voice clip to 24kHz mono WAV and activate it for cloning."""
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    target_wav = VOICES_DIR / f"{profile_name}.wav"
    target_txt = VOICES_DIR / f"{profile_name}.txt"

    # Trim silence, loudness-normalize, cap to 12 seconds for crisp zero-shot F5-TTS cloning
    subprocess.run(
        [
            FFMPEG_BIN,
            "-y",
            "-i",
            str(source_audio),
            "-t",
            "12",
            "-af",
            "silenceremove=start_periods=1:start_silence=0.15:start_threshold=-45dB,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(target_wav),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )

    transcript = (transcript_override or "").strip()
    if not transcript:
        transcript = transcribe_audio(target_wav, language="ru").strip()
    if not transcript:
        transcript = "Привет, это мой новый голос для общения в телеграме."
    target_txt.write_text(transcript, encoding="utf-8")

    profiles = load_voice_profiles()
    profiles.setdefault("profiles", {})[profile_name] = {
        "label": "Пользовательский голос" if profile_name == "custom" else profile_name,
        "audio": target_wav.name,
        "transcript_file": target_txt.name,
    }
    profiles["active"] = profile_name
    save_voice_profiles(profiles)
    LOGGER.info("Activated custom voice profile %r (%s)", profile_name, target_wav)
    return target_wav, transcript


def reset_active_voice() -> str:
    """Switch active profile back to default."""
    profiles = load_voice_profiles()
    profiles["active"] = "default"
    save_voice_profiles(profiles)
    return "default"


def _load_f5_runtime() -> dict[str, Any]:
    global _F5_RUNTIME
    if _F5_RUNTIME is not None:
        return _F5_RUNTIME
    with _MODEL_LOCK:
        if _F5_RUNTIME is not None:
            return _F5_RUNTIME
        import torch
        from huggingface_hub import hf_hub_download
        from f5_tts.infer.utils_infer import load_model, load_vocoder
        from f5_tts.model import DiT

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model_path = hf_hub_download(repo_id=F5_DEFAULT_REPO, filename=F5_DEFAULT_WEIGHTS)
        vocab_path = hf_hub_download(repo_id=F5_DEFAULT_REPO, filename=F5_DEFAULT_VOCAB)
        vocoder = load_vocoder(vocoder_name="vocos", device=device)
        model_cfg = dict(
            dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4
        )
        model = load_model(
            DiT,
            model_cfg,
            model_path,
            vocab_file=vocab_path,
            device=device,
        )
        _F5_RUNTIME = {
            "device": device,
            "model": model,
            "vocoder": vocoder,
        }
        LOGGER.info("Loaded F5-TTS Russian model %s on %s", F5_DEFAULT_REPO, device)
        return _F5_RUNTIME


def _synthesize_f5_raw(
    spoken_text: str,
    ref_audio: Path,
    ref_text: str,
    output_wav: Path,
    *,
    speed: float = 1.02,
) -> Path:
    import soundfile as sf
    from f5_tts.infer.utils_infer import infer_process

    runtime = _load_f5_runtime()
    accented_ref = _apply_ruaccent(ref_text)
    accented_gen = _apply_ruaccent(spoken_text)

    wave, sr, _ = infer_process(
        ref_audio=str(ref_audio),
        ref_text=accented_ref,
        gen_text=accented_gen,
        model_obj=runtime["model"],
        vocoder=runtime["vocoder"],
        device=runtime["device"],
        nfe_step=24,
        speed=speed,
    )
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_wav), wave, sr)
    return output_wav


def _synthesize_edge_tts_raw(
    spoken_text: str,
    output_audio: Path,
    *,
    voice: str = "ru-RU-DmitryNeural",
) -> Path:
    import edge_tts

    output_audio.parent.mkdir(parents=True, exist_ok=True)

    async def _run() -> None:
        comm = edge_tts.Communicate(spoken_text, voice=voice, rate="+4%")
        await comm.save(str(output_audio))

    asyncio.run(_run())
    return output_audio


def synthesize_telegram_voice(
    raw_text: str,
    output_ogg: Path,
    *,
    prefer_local_clone: bool = True,
    profile_override: str | None = None,
) -> VoiceResult:
    """Synthesize spoken Russian text into a Telegram-ready OGG/Opus voice message."""
    spoken_text = normalize_text_for_speech(raw_text)
    profiles = load_voice_profiles()
    edge_voice = str(profiles.get("edge_voice") or "ru-RU-DmitryNeural")
    if profile_override:
        profiles_copy = {**profiles, "active": profile_override}
        entry = (profiles_copy.get("profiles") or {}).get(profile_override) or {}
        audio_path = VOICES_DIR / str(entry.get("audio", "default.wav"))
        txt_path = VOICES_DIR / str(entry.get("transcript_file", "default.txt"))
        if audio_path.is_file():
            voice_name = profile_override
            ref_audio = audio_path
            ref_transcript = (
                txt_path.read_text(encoding="utf-8").strip() if txt_path.is_file() else "Привет."
            )
            profile_meta = entry
        else:
            voice_name, ref_audio, ref_transcript, profile_meta = get_active_voice_reference()
    else:
        voice_name, ref_audio, ref_transcript, profile_meta = get_active_voice_reference()

    speed = float(profile_meta.get("speed", 1.02))
    audio_fx = profile_meta.get("ffmpeg_fx") or None

    raw_wav = output_ogg.with_suffix(".wav")
    raw_mp3 = output_ogg.with_suffix(".mp3")
    backend = "edge_tts"

    try:
        if prefer_local_clone:
            try:
                _synthesize_f5_raw(spoken_text, ref_audio, ref_transcript, raw_wav, speed=speed)
                convert_to_telegram_ogg(raw_wav, output_ogg, audio_fx=audio_fx)
                backend = "f5_tts_ru"
            except Exception as exc:
                LOGGER.warning("F5-TTS synthesis failed (%s), falling back to edge-tts", exc)
                _synthesize_edge_tts_raw(spoken_text, raw_mp3, voice=edge_voice)
                convert_to_telegram_ogg(raw_mp3, output_ogg, audio_fx=audio_fx)
                backend = "edge_tts"
        else:
            _synthesize_edge_tts_raw(spoken_text, raw_mp3, voice=edge_voice)
            convert_to_telegram_ogg(raw_mp3, output_ogg, audio_fx=audio_fx)
    finally:
        for tmp in (raw_wav, raw_mp3):
            if tmp.is_file():
                tmp.unlink()

    duration = probe_duration_seconds(output_ogg)
    return VoiceResult(
        ogg_path=output_ogg,
        duration_seconds=duration,
        backend=backend,
        voice_name=voice_name if backend == "f5_tts_ru" else edge_voice,
        spoken_text=spoken_text,
    )
