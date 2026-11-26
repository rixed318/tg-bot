#!/usr/bin/env python3
"""Main local worker: claims messages, transcribes audio, evaluates trigger flag, and replies strictly via voice."""

from __future__ import annotations

import argparse
import base64
from dataclasses import replace
import json
import logging
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Any

from tg_bot.config import (
    AGY_BIN,
    AUDIO_SUFFIXES,
    DRIVE_SYNC_INTERVAL_SECONDS,
    GROUP_DEBOUNCE_SECONDS,
    PHOTO_SUFFIXES,
    REMOTE_PYTHON,
    REMOTE_QUEUE_SCRIPT,
    REMOTE_SSH_HOST,
    REPLY_MODEL,
    VIDEO_SUFFIXES,
    WORK_DIR,
    read_token,
)
from tg_bot.delete_messages import delete_bot_messages, record_bot_message
from tg_bot.drive_sync import (
    archive_on_drive,
    sync_all_text_contexts_in_background,
    write_local_full_context,
)
from tg_bot.media_tools import (
    download_public_media,
    find_source_video,
    generate_image,
    parse_agent_response,
    run_montage,
    run_sticker_or_gif,
    save_chat_video,
)
from tg_bot.telegram_api import (
    copy_current_message_to_group,
    download_telegram_file,
    send_animation,
    send_chat_action,
    send_document,
    send_photo,
    send_sticker,
    send_video,
    send_voice,
)
from tg_bot.transcribe import transcribe_audio
from tg_bot.trigger import (
    TriggerDecision,
    build_reply_prompt,
    collect_unanswered_messages,
    evaluate_trigger,
)
from tg_bot.voice_tts import (
    load_voice_profiles,
    reset_active_voice,
    set_custom_voice_from_audio,
    synthesize_telegram_voice,
)

LOGGER = logging.getLogger("tg_bot.worker")
IMAGE_REQUEST = re.compile(
    r"\b(изображени\w*|картин\w*|нарис\w*|мем\w*|иллюстрац\w*|фот\w*|стикер\w*|выреж\w*|монтаж\w*|рилс\w*|шортс\w*|эдит\w*)\b",
    re.IGNORECASE,
)
VISUAL_CONTEXT_REQUEST = re.compile(
    r"\b(фот\w*|изображени\w*|картин\w*|скрин\w*|нарис\w*|мем\w*|визуал\w*)\b",
    re.IGNORECASE,
)


def encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def remote_queue(*arguments: str) -> str:
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            REMOTE_SSH_HOST,
            REMOTE_PYTHON,
            REMOTE_QUEUE_SCRIPT,
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    return result.stdout.strip()


def claim_task() -> dict[str, Any] | None:
    raw = remote_queue("claim")
    return json.loads(raw) if raw and raw != "null" else None


def fetch_all_contexts() -> list[dict[str, Any]]:
    raw = remote_queue("contexts")
    return json.loads(raw) if raw else []


def group_is_muted() -> bool:
    raw = remote_queue("state")
    state = json.loads(raw) if raw else {}
    return bool(state.get("muted"))


def bypasses_group_mute(task: dict[str, Any]) -> bool:
    return str(task.get("text") or "").startswith(
        "Сгенерируй изображение строго по этому промпту:"
    )


def suppress_for_live_mute(task: dict[str, Any]) -> bool:
    return (
        int(task["chat_id"]) < 0
        and not bypasses_group_mute(task)
        and group_is_muted()
    )


def _schedule_drive_sync(*, force: bool = False) -> None:
    threading.Thread(
        target=sync_all_text_contexts_in_background,
        args=(fetch_all_contexts,),
        kwargs={"force": force},
        name="drive-context-sync",
        daemon=True,
    ).start()


def _transcribe_unanswered_audio(
    token: str,
    task: dict[str, Any],
    unanswered: list[dict[str, Any]],
    work_dir: Path,
) -> Path | None:
    """Download and transcribe audio/voice/video_note for the primary task and any un-transcribed items."""
    primary_attachment: Path | None = None
    primary_mid = int(task.get("message_id") or 0)
    chat_id = int(task["chat_id"])

    for idx, item in enumerate(unanswered):
        file_id = item.get("photo_file_id")
        if not file_id:
            continue
        item_text = str(item.get("text") or "")
        is_primary = int(item.get("message_id") or 0) == primary_mid
        looks_audio = any(
            marker in item_text
            for marker in (
                "[К сообщению прикреплено: голосовое сообщение]",
                "[К сообщению прикреплено: видеосообщение]",
                "[К сообщению прикреплено: аудиотрек]",
            )
        )
        already_transcribed = "[Расшифровка]:" in item_text or "[Расшифровка голосового" in item_text
        if not is_primary and (not looks_audio or already_transcribed):
            continue

        stem = "input" if is_primary else f"unanswered-{idx}"
        try:
            downloaded = download_telegram_file(token, str(file_id), work_dir, stem)
        except Exception as err:
            LOGGER.warning("Could not download media for msg %s: %s", item.get("message_id"), err)
            continue

        if is_primary:
            primary_attachment = downloaded
            archive_on_drive(
                downloaded,
                task,
                category="incoming",
                stable_key=str(file_id),
            )

        is_audio_file = downloaded.suffix.lower() in AUDIO_SUFFIXES or looks_audio
        if is_audio_file and not already_transcribed:
            try:
                transcript = transcribe_audio(downloaded)
                if transcript:
                    enriched = f"{item_text}\n[Расшифровка]: «{transcript}»".strip()
                    item["text"] = enriched
                    if is_primary:
                        task["text"] = enriched
                    remote_queue(
                        "update-observation",
                        "--chat-id",
                        str(chat_id),
                        "--message-id",
                        str(item["message_id"]),
                        "--text-base64",
                        encoded(enriched),
                    )
                    LOGGER.info(
                        "Persisted transcription for msg %s: %s",
                        item.get("message_id"),
                        transcript[:100],
                    )
            except Exception as err:
                LOGGER.warning("Audio transcription error for msg %s: %s", item.get("message_id"), err)

    return primary_attachment


def _ask_gemini_reply(
    task: dict[str, Any],
    *,
    decision: TriggerDecision,
    unanswered: list[dict[str, Any]],
    work_dir: Path,
    attachment: Path | None,
    context_attachments: tuple[Path, ...],
) -> str:
    timeout_seconds = 240 if IMAGE_REQUEST.search(str(task.get("text") or "")) else 180
    reply_prompt = build_reply_prompt(
        task,
        decision=decision,
        unanswered=unanswered,
        attachment=attachment,
        context_attachments=context_attachments,
        work_dir=work_dir,
    )
    command = [
        str(AGY_BIN),
        "--dangerously-skip-permissions",
        "--model",
        REPLY_MODEL,
        "--output-format",
        "json",
        "--print-timeout",
        f"{timeout_seconds}s",
        f"--print={reply_prompt}",
    ]
    result = subprocess.run(
        command,
        cwd=work_dir,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds + 30,
    )
    payload = json.loads(result.stdout)
    if payload.get("status") != "SUCCESS":
        raise RuntimeError(f"agy returned {payload.get('status')}")
    return str(payload.get("response") or "").strip()


def _send_voice_reply(
    token: str,
    task: dict[str, Any],
    spoken_text: str,
    work_dir: Path,
    *,
    reply_to_message_id: int | None,
) -> tuple[int, str]:
    """Synthesize spoken_text into OGG/Opus, send as Telegram voice, and archive to Drive."""
    send_chat_action(token, task, "record_voice")
    ogg_path = work_dir / f"voice_{task['id']}.ogg"
    voice_res = synthesize_telegram_voice(spoken_text, ogg_path)
    mid = send_voice(
        token,
        task,
        voice_res.ogg_path,
        duration_seconds=voice_res.duration_seconds,
        reply_to_message_id=reply_to_message_id,
    )
    try:
        archive_on_drive(
            voice_res.ogg_path,
            task,
            category="voice_out",
            stable_key=f"voice_out:{task['id']}:{mid}",
        )
    except Exception as exc:
        LOGGER.warning("Failed to archive outgoing voice on Drive: %s", exc)
    LOGGER.info(
        "Sent voice reply task=%s msg_id=%s dur=%ss backend=%s voice=%s",
        task["id"],
        mid,
        voice_res.duration_seconds,
        voice_res.backend,
        voice_res.voice_name,
    )
    return mid, voice_res.spoken_text


def _handle_voice_admin_command(
    token: str,
    task: dict[str, Any],
    work_dir: Path,
    claimed_ids: list[str],
) -> bool:
    """Handle /setvoice, /voices, /resetvoice in DM and reply exclusively with voice."""
    raw = str(task.get("text") or "")
    primary_mid = int(task["message_id"])

    if raw.startswith("__SET_VOICE__"):
        hint = raw.split(":", 1)[1].strip() if ":" in raw else ""
        file_id = task.get("photo_file_id")
        if not file_id:
            reply_text = "Пришли голосовое или аудиофайл прямо вместе с командой сетвойс."
        else:
            downloaded = download_telegram_file(token, str(file_id), work_dir, "voice_sample")
            _, transcript = set_custom_voice_from_audio(
                downloaded, profile_name="custom", transcript_override=hint or None
            )
            reply_text = (
                f"Готово! Я запомнил твой голос и уже говорю им. "
                f"Распознал образец так: {transcript[:120]}"
            )
        _, spoken = _send_voice_reply(token, task, reply_text, work_dir, reply_to_message_id=primary_mid)
        remote_queue(
            "complete",
            "--ids",
            ",".join(claimed_ids),
            "--response-base64",
            encoded(spoken),
            "--flag",
            "reply",
            "--reason-base64",
            encoded("настройка пользовательского голоса"),
            "--answered-up-to",
            str(primary_mid),
            "--primary-id",
            str(task["id"]),
        )
        return True

    if raw == "__LIST_VOICES__":
        profiles = load_voice_profiles()
        active = profiles.get("active", "default")
        names = ", ".join((profiles.get("profiles") or {}).keys()) or "default"
        reply_text = (
            f"Сейчас активен профиль {active}. Доступные профили: {names}. "
            "Чтобы записать свой голос, отправь голосовое с подписью сетвойс."
        )
        _, spoken = _send_voice_reply(token, task, reply_text, work_dir, reply_to_message_id=primary_mid)
        remote_queue(
            "complete",
            "--ids",
            ",".join(claimed_ids),
            "--response-base64",
            encoded(spoken),
            "--flag",
            "reply",
            "--reason-base64",
            encoded("список голосовых профилей"),
            "--answered-up-to",
            str(primary_mid),
            "--primary-id",
            str(task["id"]),
        )
        return True

    if raw.startswith("__SWITCH_VOICE__:"):
        target_name = raw.split(":", 1)[1].strip()
        profiles = load_voice_profiles()
        available = profiles.get("profiles") or {}
        if target_name in available:
            profiles["active"] = target_name
            from tg_bot.voice_tts import save_voice_profiles

            save_voice_profiles(profiles)
            reply_text = f"Переключил голос на профиль {target_name}."
        else:
            names = ", ".join(available.keys()) or "default"
            reply_text = f"Профиль {target_name} не найден. Доступны: {names}."
        _, spoken = _send_voice_reply(token, task, reply_text, work_dir, reply_to_message_id=primary_mid)
        remote_queue(
            "complete",
            "--ids",
            ",".join(claimed_ids),
            "--response-base64",
            encoded(spoken),
            "--flag",
            "reply",
            "--reason-base64",
            encoded(f"переключение голоса на {target_name}"),
            "--answered-up-to",
            str(primary_mid),
            "--primary-id",
            str(task["id"]),
        )
        return True

    if raw == "__RESET_VOICE__":
        reset_active_voice()
        reply_text = "Сбросил голос обратно на стандартный профиль."
        _, spoken = _send_voice_reply(token, task, reply_text, work_dir, reply_to_message_id=primary_mid)
        remote_queue(
            "complete",
            "--ids",
            ",".join(claimed_ids),
            "--response-base64",
            encoded(spoken),
            "--flag",
            "reply",
            "--reason-base64",
            encoded("сброс голоса на стандартный"),
            "--answered-up-to",
            str(primary_mid),
            "--primary-id",
            str(task["id"]),
        )
        return True

    return False


def _send_large_document(task: dict[str, Any], path: Path, reply_to_message_id: int | None) -> int:
    command = [
        "/home/rus/.local/share/rixed-joke-userbot/venv/bin/python",
        str(Path(__file__).resolve().with_name("premium_transport.py")),
        "--chat-id",
        str(task["chat_id"]),
        "--file",
        str(path),
    ]
    if reply_to_message_id is not None:
        command.extend(["--reply-to", str(reply_to_message_id)])
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=3600)
    mid = int(result.stdout.strip().splitlines()[-1])
    record_bot_message(int(task["chat_id"]), mid, int(task.get("id") or 0))
    return mid


def process_one(token: str) -> bool:
    task = claim_task()
    if task is None:
        return False

    # Small debounce if a group message just landed moments ago to catch rapid consecutive bubbles
    if int(task.get("chat_id") or 0) < 0 and not task.get("reply_to_is_bot") and not task.get("is_mention"):
        time.sleep(min(GROUP_DEBOUNCE_SECONDS, 4.0))

    task_id = str(task["id"])
    chat_id = int(task["chat_id"])
    is_group = chat_id < 0
    claimed_ids = [str(i) for i in (task.get("claimed_ids") or [task["id"]])]
    unanswered = collect_unanswered_messages(task)

    try:
        WORK_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"task-{task_id}-", dir=WORK_DIR) as directory:
            work_dir = Path(directory)
            (work_dir / "outbox").mkdir(mode=0o700)

            if _handle_voice_admin_command(token, task, work_dir, claimed_ids):
                _schedule_drive_sync()
                return True

            # 1. Read & transcribe incoming voice/audio messages FIRST and persist to DB/Drive!
            attachment = _transcribe_unanswered_audio(token, task, unanswered, work_dir)
            user_text = str(task.get("text") or "")

            # 2. Download recent visual context attachments if needed and write UNLIMITED full-context.jsonl
            needs_visual = bool(
                attachment
                or IMAGE_REQUEST.search(user_text)
                or VISUAL_CONTEXT_REQUEST.search(user_text)
            )
            context_media = (
                [item for item in task.get("context", []) if item.get("photo_file_id")][-2:]
                if needs_visual
                else []
            )
            context_attachments: list[Path] = []
            for idx, item in enumerate(context_media, start=1):
                try:
                    context_attachments.append(
                        download_telegram_file(
                            token, str(item["photo_file_id"]), work_dir, f"context-{idx}"
                        )
                    )
                except Exception:
                    LOGGER.warning("Could not download context media %s", idx, exc_info=True)
            write_local_full_context(task, work_dir, tuple(context_attachments))

            if suppress_for_live_mute(task):
                remote_queue(
                    "complete",
                    "--ids",
                    ",".join(claimed_ids),
                    "--response-base64",
                    encoded("[[muted]]"),
                    "--flag",
                    "skip",
                    "--reason-base64",
                    encoded("группа на паузе (/mute)"),
                )
                _schedule_drive_sync()
                return True

            # 3. Trigger evaluation: model reads every message and sets its own reply/skip flag
            decision = evaluate_trigger(task, work_dir, unanswered=unanswered)
            LOGGER.info(
                "Trigger decision chat=%s task=%s flag=%s targets=%s reason=%r",
                chat_id,
                task_id,
                decision.flag,
                decision.target_message_ids,
                decision.reason,
            )

            if not decision.should_reply:
                remote_queue(
                    "complete",
                    "--ids",
                    ",".join(claimed_ids),
                    "--response-base64",
                    encoded("[[skip]]"),
                    "--flag",
                    "skip",
                    "--reason-base64",
                    encoded(decision.reason),
                )
                _schedule_drive_sync()
                return True

            # 4. Generate ONE consolidated spoken reply covering the triggered messages
            send_chat_action(token, task, "record_voice")
            raw_reply = _ask_gemini_reply(
                task,
                decision=decision,
                unanswered=unanswered,
                work_dir=work_dir,
                attachment=attachment,
                context_attachments=tuple(context_attachments),
            )

            if raw_reply == "[[skip]]" and not decision.explicit:
                remote_queue(
                    "complete",
                    "--ids",
                    ",".join(claimed_ids),
                    "--response-base64",
                    encoded("[[skip]]"),
                    "--flag",
                    "skip",
                    "--reason-base64",
                    encoded("модель пропустила на этапе генерации ответа"),
                )
                _schedule_drive_sync()
                return True

            parsed = parse_agent_response(raw_reply or "Слушаю, я на связи.", work_dir)
            if suppress_for_live_mute(task):
                remote_queue(
                    "complete",
                    "--ids",
                    ",".join(claimed_ids),
                    "--response-base64",
                    encoded("[[muted]]"),
                    "--flag",
                    "skip",
                    "--reason-base64",
                    encoded("группа переведена на паузу"),
                )
                _schedule_drive_sync()
                return True

            # 5. Execute optional media/delete actions before sending the voice reply
            generated_photo: Path | None = None
            downloaded_files: tuple[Path, ...] = ()
            copied_group_message_id: int | None = None

            outbox = work_dir / "outbox"
            for p in sorted(outbox.glob("*")):
                if p.is_file() and p.stat().st_size > 0:
                    if p.suffix.lower() in PHOTO_SUFFIXES and p not in parsed.photos and "sticker" not in p.name.lower():
                        parsed = replace(parsed, photos=(*parsed.photos, p))
                    elif p.suffix.lower() in (VIDEO_SUFFIXES | AUDIO_SUFFIXES | {".pdf", ".zip", ".webp"}) and p not in downloaded_files:
                        downloaded_files = (*downloaded_files, p)
                        save_chat_video(task, p)
                        archive_on_drive(
                            p,
                            task,
                            category="montages" if p.suffix.lower() in VIDEO_SUFFIXES else "downloads",
                            stable_key=f"outbox:{task_id}:{p.name}",
                        )

            if parsed.image_prompt:
                try:
                    generated_photo = generate_image(parsed.image_prompt, work_dir)
                    archive_on_drive(
                        generated_photo,
                        task,
                        category="generated",
                        stable_key=f"generated:{task_id}",
                    )
                except Exception as exc:
                    LOGGER.warning("Image generation failed for task %s: %s", task_id, exc)

            if parsed.download_url:
                downloaded_files = download_public_media(parsed.download_url, work_dir)
                for index, path in enumerate(downloaded_files, start=1):
                    save_chat_video(task, path)
                    archive_on_drive(
                        path,
                        task,
                        category="downloads",
                        stable_key=f"download:{task_id}:{index}:{parsed.download_url}",
                    )

            if (parsed.make_sticker or parsed.cutout_photo) and not any(
                "sticker" in p.name.lower() or p.suffix.lower() == ".webp" for p in downloaded_files
            ):
                source_media = attachment or (context_attachments[0] if context_attachments else None)
                if source_media:
                    is_vid = source_media.suffix.lower() in VIDEO_SUFFIXES
                    out_sticker = work_dir / "outbox" / (
                        f"sticker_{task_id}.webm" if is_vid else f"sticker_{task_id}.webp"
                    )
                    mode = "video_sticker" if is_vid else "sticker"
                    try:
                        run_sticker_or_gif(source_media, out_sticker, mode)
                        if out_sticker.is_file():
                            downloaded_files = (*downloaded_files, out_sticker)
                            archive_on_drive(
                                out_sticker, task, category="stickers", stable_key=f"sticker:{task_id}"
                            )
                    except Exception:
                        LOGGER.exception("Sticker creation failed for task %s", task_id)

            if parsed.make_gif and not any(
                "anim" in p.name.lower() or p.suffix.lower() == ".gif" for p in downloaded_files
            ):
                source_video = attachment or (context_attachments[0] if context_attachments else None)
                if source_video:
                    out_anim = work_dir / "outbox" / f"anim_{task_id}.mp4"
                    try:
                        run_sticker_or_gif(source_video, out_anim, "gif")
                        if out_anim.is_file():
                            downloaded_files = (*downloaded_files, out_anim)
                            archive_on_drive(
                                out_anim, task, category="animations", stable_key=f"animation:{task_id}"
                            )
                    except Exception:
                        LOGGER.exception("GIF creation failed for task %s", task_id)

            if parsed.montage_video and not any(
                p.suffix.lower() in VIDEO_SUFFIXES
                and "sticker" not in p.name.lower()
                and "anim" not in p.name.lower()
                for p in downloaded_files
            ):
                source_video = attachment if (attachment and attachment.suffix.lower() in VIDEO_SUFFIXES) else None
                style = parsed.montage_style or "auto"
                hook = parsed.montage_hook or ""
                if not source_video:
                    source_video, r_style, r_hook = find_source_video(task, parsed.montage_target, work_dir)
                    style = parsed.montage_style or r_style or "auto"
                    hook = parsed.montage_hook or r_hook or ""
                if source_video:
                    out_montage = work_dir / "outbox" / f"montage_{task_id}.mp4"
                    try:
                        run_montage(source_video, out_montage, hook, style)
                        if out_montage.is_file():
                            downloaded_files = (*downloaded_files, out_montage)
                            save_chat_video(task, out_montage)
                            archive_on_drive(
                                out_montage, task, category="montages", stable_key=f"montage:{task_id}"
                            )
                    except Exception:
                        LOGGER.exception("Montage failed for task %s", task_id)

            if parsed.copy_current_to_group:
                copied_group_message_id = copy_current_message_to_group(token, task)

            if parsed.delete_all_bot_messages:
                delete_bot_messages(chat_id, count=None, token=token)
            elif parsed.delete_previous:
                delete_bot_messages(chat_id, count=1, token=token)

            # 6. Deliver exclusively as a Telegram VOICE message (+ optional requested media attachments)
            anchor_msg_id = (
                max(decision.target_message_ids)
                if decision.target_message_ids
                else int(task["message_id"])
            )
            voice_msg_id, spoken_transcript = _send_voice_reply(
                token,
                task,
                parsed.text,
                work_dir,
                reply_to_message_id=anchor_msg_id,
            )

            outgoing_photos = (
                (*parsed.photos, generated_photo)
                if generated_photo is not None
                else parsed.photos
            )
            for p in outgoing_photos:
                if p.suffix.lower() == ".webp" or "sticker" in p.name.lower():
                    send_sticker(token, task, p, voice_msg_id)
                else:
                    send_photo(token, task, p, voice_msg_id)

            delivery_task = task
            delivery_reply_id: int | None = voice_msg_id
            if parsed.download_to_group:
                target_group = int(task.get("configured_group_id") or -1003800116774)
                if target_group >= 0:
                    raise RuntimeError("Configured target group is missing")
                delivery_task = {**task, "chat_id": target_group, "thread_id": None}
                delivery_reply_id = None

            for path in downloaded_files:
                sfx = path.suffix.lower()
                name_lower = path.name.lower()
                if sfx == ".webp" or "sticker" in name_lower:
                    send_sticker(token, delivery_task, path, delivery_reply_id)
                elif sfx == ".gif" or (sfx == ".mp4" and "anim" in name_lower):
                    send_animation(token, delivery_task, path, delivery_reply_id)
                elif sfx in VIDEO_SUFFIXES and path.stat().st_size <= 49 * 1024 * 1024:
                    send_video(token, delivery_task, path, delivery_reply_id)
                elif path.stat().st_size <= 49 * 1024 * 1024:
                    send_document(token, delivery_task, path, delivery_reply_id)
                else:
                    _send_large_document(delivery_task, path, delivery_reply_id)

            archive_text = spoken_transcript
            photo_count = len(outgoing_photos)
            if photo_count:
                archive_text += f"\n[photos:{photo_count}]"
            if downloaded_files:
                archive_text += f"\n[downloads:{len(downloaded_files)}]"
            if copied_group_message_id is not None:
                archive_text += f"\n[copied-to-group:{copied_group_message_id}]"

            answered_watermark = max(
                int(task["message_id"]),
                max(decision.target_message_ids) if decision.target_message_ids else 0,
            )
            remote_queue(
                "complete",
                "--ids",
                ",".join(claimed_ids),
                "--response-base64",
                encoded(archive_text),
                "--flag",
                "reply",
                "--reason-base64",
                encoded(decision.reason),
                "--answered-up-to",
                str(answered_watermark),
                "--primary-id",
                str(task["id"]),
            )
            _schedule_drive_sync()
            return True

    except Exception as error:
        LOGGER.exception("Task %s failed", task_id)
        remote_queue("retry", "--ids", ",".join(claimed_ids), "--error-base64", encoded(str(error)))
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice-first Gemini Telegram worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=5)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    token = read_token()
    next_context_sync = 0.0
    while True:
        if time.monotonic() >= next_context_sync:
            _schedule_drive_sync()
            next_context_sync = time.monotonic() + DRIVE_SYNC_INTERVAL_SECONDS
        try:
            handled = process_one(token)
        except Exception:
            LOGGER.exception("Queue poll failed; retrying without restarting the worker")
            handled = False
        if args.once:
            return
        if not handled:
            time.sleep(max(2, args.interval))


if __name__ == "__main__":
    main()
