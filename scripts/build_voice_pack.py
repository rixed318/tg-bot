#!/usr/bin/env python3
"""Download, shape, configure, and render demos for all 11 character voices + Anya."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import urllib.request

from tg_bot.config import FFMPEG_BIN, VOICES_DIR
from tg_bot.voice_tts import (
    _synthesize_edge_tts_raw,
    load_voice_profiles,
    save_voice_profiles,
    synthesize_telegram_voice,
)

HF_BASE = "https://huggingface.co/datasets/langswap/dialogs-ru-emotional-conversations/resolve/main"
GH_KLAJP = "https://raw.githubusercontent.com/klajp-ship/project-8f92a7b1/main"


def _pitch_filter(semitones: float) -> str:
    factor = 2.0 ** (semitones / 12.0)
    tempo = 1.0 / factor
    # Clamp atempo into [0.5, 2.0] (our shifts are within [-6, +6] semitones -> [0.707, 1.414])
    return f"asetrate=24000*{factor:.5f},aresample=24000,atempo={tempo:.5f}"


CHARACTERS: list[dict[str, object]] = [
    {
        "id": "ded",
        "label": "Дед Геннадий",
        "aliases": ["дед", "геннадий", "дед геннадий", "старик"],
        "source_url": f"{HF_BASE}/wavs/masha_dima_part1_101.wav",
        "transcript": "Всё ради прибыли, как всегда. Никакого уважения к тому, что важно для обычных людей.",
        "ref_fx": f"{_pitch_filter(-3.0)},highpass=f=130,lowpass=f=3600,acompressor=threshold=-16dB:ratio=4",
        "ffmpeg_fx": f"{_pitch_filter(-1.2)},highpass=f=120,lowpass=f=3800",
        "speed": 0.91,
        "demo": "Опять вы в своём Блендере ерундой страдаете! В наше время на одном полигоне шедевры делали, а тут вирам им подавай!",
    },
    {
        "id": "gopnik",
        "label": "Гопник",
        "aliases": ["гопник", "пацан", "быдло"],
        "source_url": f"{HF_BASE}/wavs/masha_dima_part1_85.wav",
        "transcript": "Ох, это ужасно. Столько воспоминаний связано с тем местом. Наши школьные годы были особенными.",
        "ref_fx": f"{_pitch_filter(-1.2)},equalizer=f=220:width_type=o:width=1.5:g=4,acompressor=threshold=-14dB:ratio=4",
        "ffmpeg_fx": "equalizer=f=240:width_type=o:width=1.5:g=3,acompressor=threshold=-15dB:ratio=3",
        "speed": 1.08,
        "demo": "Слышь, братан, чё там по сетке? Если ещё раз булеан криво ляжет, я твой куб на ноль помножу, понял меня?",
    },
    {
        "id": "anime",
        "label": "Анимешница",
        "aliases": ["анимешница", "аниме", "тян", "тянка"],
        "source_url": f"{HF_BASE}/wavs/masha_dima_part5_53.wav",
        "transcript": "Спасибо, Дима! Это очень мило с твоей стороны. Давай, если что, переключимся на ты и начнём планировать тренировки вместе?",
        "ref_fx": f"{_pitch_filter(3.8)},equalizer=f=3200:width_type=o:width=1.5:g=3",
        "ffmpeg_fx": f"{_pitch_filter(1.5)},equalizer=f=3500:width_type=o:width=1.5:g=2",
        "speed": 1.14,
        "demo": "Няя, ребята, вы видели какой там сабсёрф на модельке?! Прямо супер кавайно получилось, я щас в обморок упаду от радости!",
    },
    {
        "id": "shizo",
        "label": "Шизофилософ",
        "aliases": ["шизофилософ", "шиз", "философ"],
        "source_url": f"{HF_BASE}/wavs/masha_dima_part1_71.wav",
        "transcript": "Это действительно печально, Маша. Такие места всегда хранят частичку души и много воспоминаний. Мне жаль.",
        "ref_fx": f"{_pitch_filter(-1.5)},lowpass=f=4500",
        "ffmpeg_fx": "aecho=0.8:0.88:42:0.28,equalizer=f=350:width_type=o:width=1.5:g=3",
        "speed": 0.88,
        "demo": "А вы задумывались, что дефолтный куб в Блендере — это мы сами, пока нас не удалили нажатием кнопки икс?",
    },
    {
        "id": "skuf",
        "label": "Скуф",
        "aliases": ["скуф"],
        "source_url": f"{HF_BASE}/wavs/masha_dima_part1_165.wav",
        "transcript": "Ничего себе, это прямо совсем перебор. Не думал, что всё зайдёт так далеко.",
        "ref_fx": f"{_pitch_filter(-2.2)},equalizer=f=180:width_type=o:width=1.5:g=4,lowpass=f=4200",
        "ffmpeg_fx": f"{_pitch_filter(-0.8)},equalizer=f=160:width_type=o:width=1.5:g=3",
        "speed": 0.93,
        "demo": "Ой да ладно вам, какие ноды в час ночи? Пивка бы лучше бахнули и в танки, а завтра этот ваш рендер сам докрутится.",
    },
    {
        "id": "altushka",
        "label": "Альтушка",
        "aliases": ["альтушка", "альт"],
        "source_url": f"{HF_BASE}/wavs/masha_dima_part4_118.wav",
        "transcript": "Да уж, не самое приятное приключение. А как вы решили эту проблему?",
        "ref_fx": f"{_pitch_filter(-1.4)},lowpass=f=5000",
        "ffmpeg_fx": "equalizer=f=280:width_type=o:width=1.5:g=2",
        "speed": 0.95,
        "demo": "Офигеть, конечно, очередной шедевр из кубиков. Прям вдохновляет закрыть чат и пойти смотреть в стену.",
    },
    {
        "id": "tamada",
        "label": "Тамада",
        "aliases": ["тамада", "ведущий"],
        "source_url": f"{HF_BASE}/wavs/masha_dima_part4_169.wav",
        "transcript": "А ещё я обожаю петербургские кафе с их ароматным кофе и десертами. Так приятно провести время, наслаждаясь вкусной выпечкой.",
        "ref_fx": "acompressor=threshold=-14dB:ratio=4,equalizer=f=2800:width_type=o:width=1.5:g=4",
        "ffmpeg_fx": "acompressor=threshold=-12dB:ratio=5,equalizer=f=2500:width_type=o:width=1.5:g=3",
        "speed": 1.10,
        "demo": "Дорогие друзья! Поднимем же виртуальные бокалы за тех, кто не забывает сохранять бленд-файл перед рендером! Горько!",
    },
    {
        "id": "robot",
        "label": "Робот",
        "aliases": ["робот", "киборг", "бот"],
        "source_url": f"{HF_BASE}/wavs/masha_dima_part1_368.wav",
        "transcript": "Это точно, здесь столько всего интересного. Я даже не знаю, с чего начать.",
        "ref_fx": "highpass=f=220,lowpass=f=3400,acompressor=threshold=-14dB:ratio=6",
        "ffmpeg_fx": "flanger=delay=3:depth=3:regen=78:width=80:speed=4,aecho=0.8:0.5:12:0.45,equalizer=f=1300:width_type=o:width=1.2:g=5",
        "speed": 1.00,
        "demo": "Директива принята. Анализ полигональной сетки выявил критический дефицит вкуса. Рекомендую перезагрузить сцену.",
    },
    {
        "id": "arsen",
        "label": "Арсен Маркарян",
        "aliases": ["арсен", "маркарян", "арсен маркарян", "коуч"],
        "source_url": f"{GH_KLAJP}/voices/ref_voice_2.wav",
        "transcript": "Айсберг набора мышечной массы. Будут различные способы, очевидные и вообще неочевидные, которые помогут вам нарастить мышечную массу.",
        "ref_fx": f"{_pitch_filter(-1.8)},equalizer=f=150:width_type=o:width=1.5:g=4,acompressor=threshold=-14dB:ratio=4",
        "ffmpeg_fx": f"{_pitch_filter(-0.8)},acompressor=threshold=-13dB:ratio=4,equalizer=f=160:width_type=o:width=1.5:g=3",
        "speed": 1.03,
        "demo": "Запомни простую базу, брат: пока ты жалуешься на вылеты Сайклза, сильный человек уже отрендерил сцену и пошёл делать подход!",
    },
    {
        "id": "kot",
        "label": "Кот",
        "aliases": ["кот", "котик", "мяу"],
        "source_url": f"{HF_BASE}/wavs/masha_dima_part2_122.wav",
        "transcript": "Великолепная идея! Вперёд, к новым вершинам в английском!",
        "ref_fx": f"{_pitch_filter(4.6)},equalizer=f=2600:width_type=o:width=1.5:g=3",
        "ffmpeg_fx": f"{_pitch_filter(1.8)},equalizer=f=2400:width_type=o:width=1.5:g=2",
        "speed": 1.10,
        "demo": "Мяу, кожаные. Я тут случайно по клавиатуре прошёлся и удалил ваш меш. Ни о чём не жалею, идите кормить меня.",
    },
    {
        "id": "demon",
        "label": "Демон",
        "aliases": ["демон", "сатана", "дьявол", "ад"],
        "source_url": f"{GH_KLAJP}/voices/ref_voice_1.wav",
        "transcript": "То есть я считаю, что описывая историю славян, достаточно было указать на наличие каких-то славянских культур.",
        "ref_fx": f"{_pitch_filter(-4.5)},equalizer=f=100:width_type=o:width=1.5:g=6",
        "ffmpeg_fx": f"{_pitch_filter(-2.2)},aecho=0.8:0.85:45|85:0.35|0.25,equalizer=f=95:width_type=o:width=1.5:g=5",
        "speed": 0.90,
        "demo": "Смертные... ваши полигоны принадлежат бездне! За каждый н-гон без фаски вы будете вечно запекать карты нормалей в аду!",
    },
]


def _download_or_synth(spec: dict[str, object], raw_out: Path) -> None:
    if "source_url" in spec:
        subprocess.run(
            [
                "curl",
                "-sSL",
                "--retry",
                "3",
                "--max-time",
                "30",
                "--fail",
                str(spec["source_url"]),
                "-o",
                str(raw_out),
            ],
            check=True,
        )
    else:
        mp3_tmp = raw_out.with_suffix(".mp3")
        _synthesize_edge_tts_raw(
            str(spec["transcript"]), mp3_tmp, voice=str(spec["source_edge"])
        )
        subprocess.run(
            [FFMPEG_BIN, "-y", "-i", str(mp3_tmp), "-ac", "1", "-ar", "24000", str(raw_out)],
            check=True,
            capture_output=True,
        )
        mp3_tmp.unlink(missing_ok=True)


def main() -> None:
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    demos_dir = VOICES_DIR / "demos"
    demos_dir.mkdir(parents=True, exist_ok=True)
    raw_cache = VOICES_DIR / "_raw_tmp"
    raw_cache.mkdir(parents=True, exist_ok=True)

    profiles = load_voice_profiles()
    entries = profiles.setdefault("profiles", {})

    # Register Anya profile using female studio base + her avatar folder
    entries["anya"] = {
        "label": "Аня",
        "aliases": ["аня", "анечка", "anya"],
        "audio": "masha.wav",
        "transcript_file": "masha.txt",
        "avatar_dir": "avatars/anya",
        "speed": 1.03,
    }

    for spec in CHARACTERS:
        cid = str(spec["id"])
        raw_path = raw_cache / f"{cid}_raw.wav"
        _download_or_synth(spec, raw_path)

        out_wav = VOICES_DIR / f"{cid}.wav"
        out_txt = VOICES_DIR / f"{cid}.txt"
        ref_fx = str(spec.get("ref_fx") or "")
        af = (
            f"silenceremove=start_periods=1:start_silence=0.1:start_threshold=-45dB,{ref_fx},loudnorm=I=-16:TP=-1.5:LRA=11"
            if ref_fx
            else "silenceremove=start_periods=1:start_silence=0.1:start_threshold=-45dB,loudnorm=I=-16:TP=-1.5:LRA=11"
        )
        subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-i",
                str(raw_path),
                "-t",
                "10",
                "-af",
                af,
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                str(out_wav),
            ],
            check=True,
            capture_output=True,
        )
        out_txt.write_text(str(spec["transcript"]), encoding="utf-8")

        entries[cid] = {
            "label": spec["label"],
            "aliases": spec["aliases"],
            "audio": out_wav.name,
            "transcript_file": out_txt.name,
            "speed": spec["speed"],
            "ffmpeg_fx": spec.get("ffmpeg_fx", ""),
        }
        print(f"Prepared voice profile: {cid} ({spec['label']})")

    save_voice_profiles(profiles)

    # Render demo voice notes for all 11 characters
    for spec in CHARACTERS:
        cid = str(spec["id"])
        demo_ogg = demos_dir / f"{cid}.ogg"
        res = synthesize_telegram_voice(
            str(spec["demo"]),
            demo_ogg,
            prefer_local_clone=True,
            profile_override=cid,
        )
        print(
            f"Demo OK: {cid} -> {demo_ogg.name} ({res.duration_seconds}s, {res.backend}, {demo_ogg.stat().st_size}B)"
        )

    for p in raw_cache.glob("*"):
        p.unlink()
    raw_cache.rmdir()


if __name__ == "__main__":
    main()
