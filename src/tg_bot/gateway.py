#!/usr/bin/env python3
"""Standalone Telegram ingress for the Blender group voice-first Gemini bot."""

from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ChatMemberStatus, ChatType, MessageEntityType
from aiogram.filters import Command
from aiogram.types import BotCommand, Message

try:
    from tg_bot.queue_db import (
        enqueue,
        get_group,
        is_muted,
        remember_message,
        remember_participant,
        set_group,
        set_muted,
    )
except ImportError:
    from joke_bot_queue import (
        enqueue,
        get_group,
        is_muted,
        remember_message,
        remember_participant,
        set_group,
        set_muted,
    )

LOGGER = logging.getLogger("rixed-joke-bot")


async def is_admin(message: Message, bot: Bot) -> bool:
    if message.from_user is None:
        return False
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}


async def is_configured_group_member(bot: Bot, user_id: int) -> bool:
    configured = get_group()
    if not configured:
        return False
    member = await bot.get_chat_member(configured[0], user_id)
    return member.status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.RESTRICTED,
    }


async def is_configured_group_admin(bot: Bot, user_id: int) -> bool:
    configured = get_group()
    if not configured:
        return False
    member = await bot.get_chat_member(configured[0], user_id)
    return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}


def _extract_attachment(message: Message) -> tuple[str | None, str | None]:
    if message.photo:
        return message.photo[-1].file_id, "фотография"
    if message.audio:
        fid = message.audio.file_id if (message.audio.file_size or 0) <= 20 * 1024 * 1024 else None
        return fid, "аудиотрек"
    if message.voice:
        return message.voice.file_id, "голосовое сообщение"
    if message.video_note:
        fid = (
            message.video_note.file_id
            if (message.video_note.file_size or 0) <= 20 * 1024 * 1024
            else None
        )
        return fid, "видеосообщение"
    if message.video:
        fid = message.video.file_id if (message.video.file_size or 0) <= 20 * 1024 * 1024 else None
        return fid, "видео"
    if message.animation:
        return None, "анимация/GIF"
    if (
        message.document
        and (message.document.mime_type or "").startswith("image/")
        and (message.document.file_size or 0) <= 10 * 1024 * 1024
    ):
        return message.document.file_id, "изображение-файл"
    if message.document:
        return None, "документ/файл"
    return None, None


def _has_bot_mention(message: Message, bot_username: str | None) -> bool:
    if not bot_username:
        return False
    target = f"@{bot_username.lower()}"
    raw_text = message.text or message.caption or ""
    if target in raw_text.lower():
        return True
    entities = message.entities or message.caption_entities or []
    for entity in entities:
        if entity.type == MessageEntityType.MENTION:
            fragment = raw_text[entity.offset : entity.offset + entity.length].lower()
            if fragment == target:
                return True
    return False


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is missing")
    bot = Bot(token)
    me = await bot.get_me()
    await bot.set_my_commands(
        [
            BotCommand(command="setup", description="Привязать эту группу"),
            BotCommand(command="start", description="Разрешить личный диалог"),
            BotCommand(command="status", description="Показать состояние"),
            BotCommand(command="mute", description="Поставить бота на паузу"),
            BotCommand(command="unmute", description="Вернуть бота в разговор"),
            BotCommand(command="generate", description="Создать изображение по промпту"),
            BotCommand(command="setvoice", description="Установить голос (в ЛС или reply на ГС в группе)"),
            BotCommand(command="voices", description="Показать доступные голоса"),
            BotCommand(command="voice", description="Выбрать голос по имени"),
            BotCommand(command="resetvoice", description="Сбросить голос на стандартный"),
        ]
    )
    router = Router(name="joke-gemini-bot")

    @router.message(Command("setup"))
    async def setup(message: Message) -> None:
        if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            await message.answer("Добавьте меня в группу, выдайте права администратора и вызовите /setup там.")
            return
        if not await is_admin(message, bot):
            await message.answer("Настройку может выполнить только администратор группы.")
            return
        bot_member = await bot.get_chat_member(message.chat.id, me.id)
        if bot_member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
            await message.answer("Сначала выдайте мне права администратора, затем повторите /setup.")
            return
        set_group(message.chat.id, message.chat.title or "Telegram-группа")
        await message.answer(
            "Готово. Я привязан к этой группе, общаюсь голосовыми и веду полную историю на Google Drive."
        )
        LOGGER.info("Configured group=%s title=%r", message.chat.id, message.chat.title)

    @router.message(Command("start"))
    async def start_private(message: Message) -> None:
        if message.chat.type != ChatType.PRIVATE or message.from_user is None:
            return
        if not get_group():
            await message.answer("Сначала администратор должен настроить основную группу через /setup.")
            return
        if not await is_configured_group_member(bot, message.from_user.id):
            await message.answer("Личный диалог доступен только участникам настроенной группы.")
            return
        remember_participant(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
            can_dm=True,
        )
        await message.answer(
            "Личка подключена. Здесь у нас отдельный разговор — сообщения других участников не подмешиваются."
        )

    @router.message(Command("status"))
    async def status(message: Message) -> None:
        configured = get_group()
        allowed_private = (
            message.chat.type == ChatType.PRIVATE
            and message.from_user is not None
            and await is_configured_group_member(bot, message.from_user.id)
        )
        if configured and (message.chat.id == configured[0] or allowed_private):
            mode = "пауза" if is_muted() else "активен"
            await message.answer(
                f"На связи. Режим: {mode}. Общение голосовыми, память без лимита + Google Drive."
            )

    @router.message(Command("mute"))
    async def mute(message: Message) -> None:
        configured = get_group()
        if not configured or message.chat.id != configured[0]:
            return
        if not await is_admin(message, bot):
            await message.answer("Поставить меня на паузу может только администратор.")
            return
        set_muted(True)
        await message.answer("Ушёл в режим молчаливого наблюдателя 🤐")

    @router.message(Command("unmute"))
    async def unmute(message: Message) -> None:
        configured = get_group()
        if not configured or message.chat.id != configured[0]:
            return
        if not await is_admin(message, bot):
            await message.answer("Вернуть меня в разговор может только администратор.")
            return
        set_muted(False)
        await message.answer("Я снова в эфире. Теперь отвечаю голосовыми 😎")

    @router.message(Command("setvoice"))
    async def setvoice(message: Message) -> None:
        if message.from_user is None:
            return
        configured = get_group()
        in_group = bool(configured and message.chat.id == configured[0])
        in_private = message.chat.type == ChatType.PRIVATE
        if not in_group and not in_private:
            return
        if not await is_configured_group_admin(bot, message.from_user.id):
            await message.answer("Менять голос может только администратор группы.")
            return
        source_msg = message
        file_id, _ = _extract_attachment(message)
        if not file_id and message.reply_to_message:
            source_msg = message.reply_to_message
            file_id, _ = _extract_attachment(source_msg)
        if not file_id or not (
            source_msg.voice or source_msg.audio or source_msg.video_note or source_msg.video
        ):
            await message.answer(
                "Отправь голосовое (5–20 секунд чистой речи) с подписью /setvoice "
                "или ответь командой /setvoice на любое голосовое."
            )
            return
        remember_participant(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
            can_dm=in_private,
        )
        raw_parts = (message.text or message.caption or "").split(maxsplit=1)
        transcript_hint = raw_parts[1].strip() if len(raw_parts) == 2 else ""
        task_text = f"__SET_VOICE__:{transcript_hint}" if transcript_hint else "__SET_VOICE__"
        enqueue(
            chat_id=message.chat.id,
            thread_id=message.message_thread_id,
            message_id=message.message_id,
            reply_to_message_id=None,
            sender_id=message.from_user.id,
            sender_name=message.from_user.full_name,
            text=task_text,
            reply_context=None,
            photo_file_id=file_id,
            reply_to_is_bot=True,
            is_mention=True,
        )
        LOGGER.info("Setvoice command queued message=%s sender=%s", message.message_id, message.from_user.id)

    @router.message(Command("voices"))
    async def list_voices(message: Message) -> None:
        if message.from_user is None:
            return
        configured = get_group()
        in_group = bool(configured and message.chat.id == configured[0])
        in_private = message.chat.type == ChatType.PRIVATE
        if not in_group and not in_private:
            return
        if in_private and not await is_configured_group_member(bot, message.from_user.id):
            return
        enqueue(
            chat_id=message.chat.id,
            thread_id=message.message_thread_id,
            message_id=message.message_id,
            reply_to_message_id=None,
            sender_id=message.from_user.id,
            sender_name=message.from_user.full_name,
            text="__LIST_VOICES__",
            reply_context=None,
            reply_to_is_bot=True,
            is_mention=True,
        )

    @router.message(Command("voice"))
    async def switch_voice(message: Message) -> None:
        if message.from_user is None:
            return
        configured = get_group()
        in_group = bool(configured and message.chat.id == configured[0])
        in_private = message.chat.type == ChatType.PRIVATE
        if not in_group and not in_private:
            return
        if not await is_configured_group_admin(bot, message.from_user.id):
            await message.answer("Переключать голос может только администратор группы.")
            return
        parts = (message.text or "").split(maxsplit=1)
        profile_name = parts[1].strip() if len(parts) == 2 else ""
        if not profile_name:
            await message.answer("Укажи имя профиля: /voice default или /voice custom")
            return
        enqueue(
            chat_id=message.chat.id,
            thread_id=message.message_thread_id,
            message_id=message.message_id,
            reply_to_message_id=None,
            sender_id=message.from_user.id,
            sender_name=message.from_user.full_name,
            text=f"__SWITCH_VOICE__:{profile_name}",
            reply_context=None,
            reply_to_is_bot=True,
            is_mention=True,
        )

    @router.message(Command("resetvoice"))
    async def reset_voice(message: Message) -> None:
        if message.from_user is None:
            return
        configured = get_group()
        in_group = bool(configured and message.chat.id == configured[0])
        in_private = message.chat.type == ChatType.PRIVATE
        if not in_group and not in_private:
            return
        if not await is_configured_group_admin(bot, message.from_user.id):
            await message.answer("Сбрасывать голос может только администратор группы.")
            return
        enqueue(
            chat_id=message.chat.id,
            thread_id=message.message_thread_id,
            message_id=message.message_id,
            reply_to_message_id=None,
            sender_id=message.from_user.id,
            sender_name=message.from_user.full_name,
            text="__RESET_VOICE__",
            reply_context=None,
            reply_to_is_bot=True,
            is_mention=True,
        )

    @router.message(Command("generate"))
    async def generate(message: Message) -> None:
        configured = get_group()
        if not configured or message.chat.id != configured[0] or message.from_user is None:
            return
        raw = (message.text or message.caption or "").strip()
        parts = raw.split(maxsplit=1)
        image_prompt = parts[1].strip() if len(parts) == 2 else ""
        if not image_prompt:
            await message.answer(
                "После /generate с новой строки напишите точный промпт изображения."
            )
            return
        remember_participant(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
        )
        task_text = f"Сгенерируй изображение строго по этому промпту:\n{image_prompt}"
        reply = message.reply_to_message
        remember_message(
            chat_id=message.chat.id,
            message_id=message.message_id,
            sender_id=message.from_user.id,
            sender_name=message.from_user.full_name,
            text=task_text,
            reply_to_message_id=reply.message_id if reply else None,
        )
        enqueue(
            chat_id=message.chat.id,
            thread_id=message.message_thread_id,
            message_id=message.message_id,
            reply_to_message_id=reply.message_id if reply else None,
            sender_id=message.from_user.id,
            sender_name=message.from_user.full_name,
            text=task_text,
            reply_context=(reply.text or reply.caption) if reply else None,
            reply_to_is_bot=True,
            is_mention=True,
        )
        LOGGER.info("Generate command queued message=%s", message.message_id)

    @router.message()
    async def queue_message(message: Message) -> None:
        configured = get_group()
        if not configured:
            return
        if message.from_user is None or message.from_user.is_bot:
            return
        is_group = message.chat.id == configured[0]
        is_private = message.chat.type == ChatType.PRIVATE
        if not is_group and not is_private:
            return
        if is_private and not await is_configured_group_member(bot, message.from_user.id):
            await message.answer("Я отвечаю в личке только участникам настроенной группы.")
            return
        remember_participant(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
            can_dm=is_private,
        )
        text = (message.text or message.caption or "").strip()
        photo_file_id, attachment_kind = _extract_attachment(message)
        if attachment_kind:
            marker = f"[К сообщению прикреплено: {attachment_kind}]"
            text = f"{text}\n{marker}" if text else marker
        if not text or text.startswith("/"):
            return

        reply = message.reply_to_message
        reply_to_is_bot = bool(reply and reply.from_user and reply.from_user.id == me.id)
        is_mention = _has_bot_mention(message, me.username)

        remember_message(
            chat_id=message.chat.id,
            message_id=message.message_id,
            sender_id=message.from_user.id,
            sender_name=message.from_user.full_name,
            text=text,
            photo_file_id=photo_file_id,
            reply_to_message_id=reply.message_id if reply else None,
        )
        # Group pause means silent observation, not loss of future context.
        if is_group and is_muted():
            return

        created = enqueue(
            chat_id=message.chat.id,
            thread_id=message.message_thread_id,
            message_id=message.message_id,
            reply_to_message_id=reply.message_id if reply else None,
            sender_id=message.from_user.id,
            sender_name=message.from_user.full_name,
            text=text,
            reply_context=(reply.text or reply.caption) if reply else None,
            photo_file_id=photo_file_id,
            reply_to_is_bot=reply_to_is_bot,
            is_mention=is_mention,
        )
        LOGGER.info(
            "Message %s queued=%s sender=%s reply_bot=%s mention=%s",
            message.message_id,
            created,
            message.from_user.id,
            reply_to_is_bot,
            is_mention,
        )

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await bot.delete_webhook(drop_pending_updates=False)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
