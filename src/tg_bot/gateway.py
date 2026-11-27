#!/usr/bin/env python3
"""Standalone Telegram ingress with structured /commands inline control panel."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatMemberStatus, ChatType, MessageEntityType
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

try:
    from tg_bot.queue_db import (
        enqueue,
        get_bot_settings,
        get_group,
        is_muted,
        remember_message,
        remember_participant,
        set_bot_setting,
        set_group,
        set_muted,
    )
except ImportError:
    from joke_bot_queue import (
        enqueue,
        get_bot_settings,
        get_group,
        is_muted,
        remember_message,
        remember_participant,
        set_bot_setting,
        set_group,
        set_muted,
    )

LOGGER = logging.getLogger("rixed-joke-bot")

CHARACTER_CATALOG: list[tuple[str, str]] = [
    ("default", "Тау бао"),
    ("anya", "Аня"),
    ("artem", "Артём"),
    ("karina", "Карина"),
    ("ksyusha", "Ксюша"),
    ("katya", "Катя"),
    ("nikita", "Никита"),
    ("veronika", "Вероника"),
    ("liza", "Лиза"),
    ("serezha", "Серёжа"),
    ("alena", "Алёна (бывшая)"),
    ("fedya", "Федя"),
    ("ruslan", "Руслан"),
    ("choknutiy", "Чокнутый"),
    ("kaneki", "Канеки"),
    ("haldina", "Халдина"),
    ("arsen", "Арсен Маркарян"),
    ("demon", "Демон"),
    ("custom", "Свой голос"),
]

AI_MODELS: list[tuple[str, str]] = [
    ("gemini-4-hydrogen-high", "Gemini 4 Hydrogen (High)"),
    ("gemini-4-hydrogen-medium", "Gemini 4 Hydrogen (Medium)"),
    ("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"),
    ("grok-4.6", "Grok 4.6"),
    ("grok-4.5", "Grok 4.5"),
]


def _label_for(catalog: list[tuple[str, str]], key: str, fallback: str) -> str:
    for cid, label in catalog:
        if cid == key:
            return label
    return fallback


def _build_main_panel() -> tuple[str, InlineKeyboardMarkup]:
    settings = get_bot_settings()
    muted = settings.get("muted") == "1"
    voice = settings.get("voice_profile", "default")
    persona = settings.get("persona", "default")
    ai_model = settings.get("ai_model", "gemini-4-hydrogen-high")
    avatar_on = settings.get("avatar_enabled", "1") == "1"
    speech_display = settings.get("speech_display", "quality")
    funny_on = speech_display == "funny"
    userbot_on = settings.get("use_userbot") == "1"

    status_line = "🔇 На паузе (только слушает)" if muted else "🔊 Активен (отвечает в чате)"
    fmt_line = "🎬 Кружочки-аватары" if avatar_on else "🎙 Только голосовые (ГС)"
    display_line = "🤪 Ржачный" if funny_on else "✨ Обычный (качественный)"
    account_line = "⭐ Telegram Premium (мой аккаунт)" if userbot_on else "🤖 Обычный бот"

    text = (
        "⚙️ <b>Панель управления ботом</b>\n\n"
        f"• Режим: <b>{status_line}</b>\n"
        f"• Голос: <b>{_label_for(CHARACTER_CATALOG, voice, voice)}</b>\n"
        f"• Поведение: <b>{_label_for(CHARACTER_CATALOG, persona, persona)}</b>\n"
        f"• ИИ-модель: <b>{_label_for(AI_MODELS, ai_model, ai_model)}</b>\n"
        f"• Формат: <b>{fmt_line}</b>\n"
        f"• Отображение речи: <b>{display_line}</b>\n"
        f"• Отправка: <b>{account_line}</b>"
    )
    mute_btn = (
        InlineKeyboardButton(text="🔊 Включить (Unmute)", callback_data="cmd:mute:0")
        if muted
        else InlineKeyboardButton(text="🔇 Пауза (Mute)", callback_data="cmd:mute:1")
    )
    fmt_btn = InlineKeyboardButton(
        text="🎬 Формат: Кружочки" if avatar_on else "🎙 Формат: ГС",
        callback_data=f"cmd:avatar:{'0' if avatar_on else '1'}",
    )
    display_btn = InlineKeyboardButton(
        text="🎭 Отображение речи: Ржачный" if funny_on else "🎭 Отображение речи: Обычный",
        callback_data=f"cmd:speech_display:{'quality' if funny_on else 'funny'}",
    )
    userbot_btn = InlineKeyboardButton(
        text="⭐ Отправка: Premium" if userbot_on else "🤖 Отправка: Бот",
        callback_data=f"cmd:userbot:{'0' if userbot_on else '1'}",
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [mute_btn, fmt_btn],
            [display_btn, userbot_btn],
            [
                InlineKeyboardButton(text="🎙 Голос", callback_data="cmd:menu:voice"),
                InlineKeyboardButton(text="🧠 Поведение", callback_data="cmd:menu:persona"),
            ],
            [
                InlineKeyboardButton(text="👗 Визуал", callback_data="cmd:menu:visual"),
                InlineKeyboardButton(text="🤖 Модель ИИ", callback_data="cmd:menu:ai"),
            ],
            [
                InlineKeyboardButton(text="🎮 Игры и рекорды", callback_data="cmd:menu:games"),
                InlineKeyboardButton(text="✖️ Закрыть", callback_data="cmd:close"),
            ],
        ]
    )
    return text, kb


def _build_grid_panel(
    title: str, items: list[tuple[str, str]], active_key: str, prefix: str
) -> tuple[str, InlineKeyboardMarkup]:
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for cid, label in items:
        mark = "✅ " if cid == active_key else ""
        pair.append(InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"{prefix}:{cid}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="cmd:home")])
    return title, InlineKeyboardMarkup(inline_keyboard=rows)


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
    # Keep slash menu clean: /commands opens the full structured inline panel.
    await bot.set_my_commands(
        [
            BotCommand(command="commands", description="Панель управления (голос, характер, ИИ, мут)"),
            BotCommand(command="setup", description="Привязать группу"),
            BotCommand(command="start", description="Личный диалог с ботом"),
        ]
    )
    router = Router(name="joke-gemini-bot")

    @router.message(Command("commands"))
    async def commands_panel(message: Message) -> None:
        if message.from_user is None:
            return
        configured = get_group()
        in_group = bool(configured and message.chat.id == configured[0])
        in_private = message.chat.type == ChatType.PRIVATE
        if not in_group and not in_private:
            return
        if not await is_configured_group_admin(bot, message.from_user.id):
            await message.answer("Панель управления доступна только администратору группы.")
            return
        text, kb = _build_main_panel()
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    @router.callback_query(F.data.startswith("cmd:"))
    async def handle_panel_callback(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.message is None:
            return
        data = callback.data or ""
        is_public_game = data.startswith("cmd:game:") or data == "cmd:menu:games"
        if not is_public_game and not await is_configured_group_admin(bot, callback.from_user.id):
            await callback.answer("Только для администраторов группы.", show_alert=True)
            return
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else "home"

        if action == "close":
            await callback.message.delete()
            await callback.answer()
            return
        if action == "mute" and len(parts) > 2:
            set_muted(parts[2] == "1")
            await callback.answer("Пауза включена" if parts[2] == "1" else "Бот активен")
        elif action == "avatar" and len(parts) > 2:
            set_bot_setting("avatar_enabled", "1" if parts[2] == "1" else "0")
            await callback.answer("Режим кружочков" if parts[2] == "1" else "Только ГС")
        elif action == "speech_display" and len(parts) > 2:
            mode = "funny" if parts[2] == "funny" else "quality"
            set_bot_setting("speech_display", mode)
            await callback.answer("Отображение речи: Ржачный" if mode == "funny" else "Отображение речи: Обычный")
        elif action == "userbot" and len(parts) > 2:
            enabled = parts[2] == "1"
            set_bot_setting("use_userbot", "1" if enabled else "0")
            await callback.answer("Отправка: Telegram Premium" if enabled else "Отправка: Бот")
        elif action == "delmsg":
            await callback.answer()
            if callback.message:
                try:
                    await callback.message.delete()
                except Exception:
                    pass
            return
        elif action == "previewvisual":
            settings = get_bot_settings()
            persona = settings.get("persona", "default")
            mode = settings.get("speech_display", "quality")
            roots = [
                Path("/opt/rixed-joke-bot/avatars_preview"),
                Path(__file__).resolve().parents[2] / "voices" / "avatars",
            ]
            preview_path: Path | None = None
            names = [persona] if persona and persona != "default" else ["anya", "default"]
            for root in roots:
                for nm in names:
                    if mode not in ("quality", "default", "normal"):
                        cand = root / f"{nm}_{mode}" / "neutral_closed.png"
                        if cand.is_file():
                            preview_path = cand
                            break
                    cand = root / nm / "neutral_closed.png"
                    if cand.is_file():
                        preview_path = cand
                        break
                if preview_path:
                    break
            if not preview_path:
                await callback.answer("Превью для этого образа пока нет", show_alert=True)
                return
            await callback.answer("Отправляю превью...")
            if callback.message:
                hide_kb = InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="🗑 Скрыть превью", callback_data="cmd:delmsg")]]
                )
                await callback.message.answer_photo(
                    FSInputFile(preview_path),
                    caption=f"👀 <b>Превью образа:</b> {persona} ({mode})",
                    parse_mode="HTML",
                    reply_markup=hide_kb,
                )
            return
        elif action == "setvisual" and len(parts) > 2:
            mode = parts[2] if parts[2] in ("quality", "street", "funny") else "quality"
            set_bot_setting("speech_display", mode)
            labels = {
                "quality": "Визуал: Обычный (2D)",
                "street": "Визуал: Streetwear (2D)",
                "funny": "Визуал: Ржачный",
            }
            await callback.answer(labels[mode])
            action = "menu"
            parts = ["cmd", "menu", "visual"]
        elif action == "setvoice" and len(parts) > 2:
            cid = parts[2]
            set_bot_setting("voice_profile", cid)
            await callback.answer(f"Голос: {_label_for(CHARACTER_CATALOG, cid, cid)}")
        elif action == "setpersona" and len(parts) > 2:
            cid = parts[2]
            set_bot_setting("persona", cid)
            await callback.answer(f"Поведение: {_label_for(CHARACTER_CATALOG, cid, cid)}")
        elif action == "setai" and len(parts) > 2:
            mid = parts[2]
            set_bot_setting("ai_model", mid)
            await callback.answer(f"ИИ: {_label_for(AI_MODELS, mid, mid)}")
        elif action == "game" and len(parts) > 2:
            subgame = parts[2]
            arg = parts[3] if len(parts) > 3 else ""
            try:
                from tg_bot import games as gmod
            except ImportError:
                import joke_bot_games as gmod
            u = callback.from_user
            cid = callback.message.chat.id if callback.message else u.id
            uname = u.username or ""
            dname = u.full_name or str(u.id)

            if subgame == "duel":
                gtext = gmod.play_dice_duel_round(user_id=u.id, username=uname, display_name=dname)
                gkb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="🎲 Ещё раз", callback_data="cmd:game:duel"),
                            InlineKeyboardButton(text="🏆 Рекорды", callback_data="cmd:game:top:dice_duel"),
                        ],
                        [InlineKeyboardButton(text="⬅️ Игры", callback_data="cmd:menu:games")],
                    ]
                )
            elif subgame == "snake":
                if arg in ("up", "down", "left", "right"):
                    gtext, alive = gmod.step_snake_game(
                        chat_id=cid, user_id=u.id, username=uname, display_name=dname, direction=arg
                    )
                else:
                    gtext, alive = gmod.start_snake_game(
                        chat_id=cid, user_id=u.id, username=uname, display_name=dname
                    )
                rows_kb = []
                if alive:
                    rows_kb = [
                        [InlineKeyboardButton(text="⬆️", callback_data="cmd:game:snake:up")],
                        [
                            InlineKeyboardButton(text="⬅️", callback_data="cmd:game:snake:left"),
                            InlineKeyboardButton(text="⬇️", callback_data="cmd:game:snake:down"),
                            InlineKeyboardButton(text="➡️", callback_data="cmd:game:snake:right"),
                        ],
                    ]
                rows_kb.append(
                    [
                        InlineKeyboardButton(text="🔄 Новая игра", callback_data="cmd:game:snake:start"),
                        InlineKeyboardButton(text="🏆 Топ Змейки", callback_data="cmd:game:top:snake"),
                    ]
                )
                rows_kb.append([InlineKeyboardButton(text="⬅️ Игры", callback_data="cmd:menu:games")])
                gkb = InlineKeyboardMarkup(inline_keyboard=rows_kb)
            elif subgame == "mafia":
                toast = ""
                if arg == "join":
                    toast = gmod.mafia_join(cid, u.id, uname, dname)
                    gtext = gmod.mafia_status_html(cid)
                elif arg == "leave":
                    toast = gmod.mafia_leave(cid, u.id)
                    gtext = gmod.mafia_status_html(cid)
                elif arg == "reset":
                    gmod.mafia_reset(cid)
                    gtext = gmod.mafia_status_html(cid)
                elif arg == "start":
                    ok, gtext, dms = gmod.mafia_start(cid)
                    if ok:
                        for pid, dm_text in dms:
                            try:
                                await bot.send_message(pid, dm_text, parse_mode="HTML")
                            except Exception:
                                pass
                    else:
                        toast = gtext
                        gtext = gmod.mafia_status_html(cid)
                elif arg == "vote" and len(parts) > 4:
                    gtext, _ = gmod.mafia_vote(cid, u.id, int(parts[4]))
                else:
                    gtext = gmod.mafia_status_html(cid)

                alive_players = gmod.mafia_get_alive_players(cid)
                if "Дневное голосование" in gtext and alive_players:
                    vote_rows = [
                        [
                            InlineKeyboardButton(
                                text=f"⚖️ @{p['username']}" if p.get("username") else f"⚖️ {p['display_name']}",
                                callback_data=f"cmd:game:mafia:vote:{p['user_id']}",
                            )
                        ]
                        for p in alive_players
                    ]
                    vote_rows.append([InlineKeyboardButton(text="🔄 Сбросить партию", callback_data="cmd:game:mafia:reset")])
                    gkb = InlineKeyboardMarkup(inline_keyboard=vote_rows)
                else:
                    gkb = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(text="➕ Присоединиться", callback_data="cmd:game:mafia:join"),
                                InlineKeyboardButton(text="➖ Выйти", callback_data="cmd:game:mafia:leave"),
                            ],
                            [
                                InlineKeyboardButton(text="🚀 Начать игру", callback_data="cmd:game:mafia:start"),
                                InlineKeyboardButton(text="🏆 Топ Мафии", callback_data="cmd:game:top:mafia"),
                            ],
                            [InlineKeyboardButton(text="⬅️ Игры", callback_data="cmd:menu:games")],
                        ]
                    )
                await callback.answer(toast[:180] if toast else None)
                try:
                    await callback.message.edit_text(gtext, parse_mode="HTML", reply_markup=gkb)
                except Exception:
                    pass
                return
            elif subgame == "mines":
                if arg == "tap" and len(parts) > 5:
                    gtext, grid, _ = gmod.step_mines_game(
                        chat_id=cid,
                        user_id=u.id,
                        username=uname,
                        display_name=dname,
                        row=int(parts[4]),
                        col=int(parts[5]),
                    )
                else:
                    gtext, grid, _ = gmod.start_mines_game(chat_id=cid, user_id=u.id)
                mrows = [
                    [
                        InlineKeyboardButton(
                            text=cell,
                            callback_data=f"cmd:game:mines:tap:{r_idx}:{c_idx}",
                        )
                        for c_idx, cell in enumerate(row)
                    ]
                    for r_idx, row in enumerate(grid)
                ]
                mrows.append(
                    [
                        InlineKeyboardButton(text="🔄 Новая игра", callback_data="cmd:game:mines:start"),
                        InlineKeyboardButton(text="🏆 Топ Сапёра", callback_data="cmd:game:top:mines"),
                    ]
                )
                mrows.append([InlineKeyboardButton(text="⬅️ Игры", callback_data="cmd:menu:games")])
                gkb = InlineKeyboardMarkup(inline_keyboard=mrows)
            else:
                board_game = arg if arg in ("dice_duel", "snake", "mafia", "mines") else "dice_duel"
                gtext = gmod.format_leaderboard_html(board_game)
                gkb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="🎲 Кости", callback_data="cmd:game:top:dice_duel"),
                            InlineKeyboardButton(text="🐍 Змейка", callback_data="cmd:game:top:snake"),
                        ],
                        [
                            InlineKeyboardButton(text="💣 Сапёр", callback_data="cmd:game:top:mines"),
                            InlineKeyboardButton(text="🕵️ Мафия", callback_data="cmd:game:top:mafia"),
                        ],
                        [InlineKeyboardButton(text="⬅️ Игры", callback_data="cmd:menu:games")],
                    ]
                )
            await callback.answer()
            try:
                await callback.message.edit_text(gtext, parse_mode="HTML", reply_markup=gkb)
            except Exception:
                pass
            return

        settings = get_bot_settings()
        if action == "menu" and len(parts) > 2:
            sub = parts[2]
            if sub == "visual":
                visual_items = [
                    ("quality", "✨ Обычный (2D пиксель-арт)"),
                    ("street", "🧥 Streetwear (2D наряд)"),
                    ("funny", "🤪 Ржачный (живое фото)"),
                ]
                text, kb = _build_grid_panel(
                    "👗 <b>Выбери визуальный стиль аватара:</b>",
                    visual_items,
                    settings.get("speech_display", "quality"),
                    "cmd:setvisual",
                )
                kb.inline_keyboard.insert(
                    -1,
                    [InlineKeyboardButton(text="👀 Показать превью образа", callback_data="cmd:previewvisual")],
                )
            elif sub == "voice":
                text, kb = _build_grid_panel(
                    "🎙 <b>Выбери только голос (поведение сохранится):</b>",
                    CHARACTER_CATALOG,
                    settings.get("voice_profile", "default"),
                    "cmd:setvoice",
                )
            elif sub == "persona":
                text, kb = _build_grid_panel(
                    "🧠 <b>Выбери модель поведения (голос сохранится):</b>",
                    CHARACTER_CATALOG,
                    settings.get("persona", "default"),
                    "cmd:setpersona",
                )
            elif sub == "games":
                is_priv_chat = callback.message is not None and callback.message.chat.type == ChatType.PRIVATE
                u = callback.from_user
                arcade_url = (
                    f"https://rixed.store/arcade/?uid={u.id}"
                    f"&uname={quote(u.username or '')}&dname={quote(u.full_name or '')}"
                )
                html5_btn = (
                    InlineKeyboardButton(text="🚀 Змейка 60 FPS (HTML5)", web_app=WebAppInfo(url=arcade_url))
                    if is_priv_chat
                    else InlineKeyboardButton(
                        text="🚀 Змейка 60 FPS (HTML5)",
                        url=f"https://t.me/{me.username}?start=snake",
                    )
                )
                text = (
                    "🎮 <b>Игры в чате</b>\n\n"
                    "• <b>🚀 Змейка 60 FPS (HTML5)</b> — плавный Canvas, свайпы, частицы и авто-запись в рейтинг!\n"
                    "• <b>🐍 Змейка в чате</b> — быстрая игра прямо в сообщении.\n"
                    "• <b>💣 Сапёр 5×5</b> — открывай клетки без мин, +60 очков за победу.\n"
                    "• <b>🕵️ Мафия</b> — лобби для компании (роли в ЛС + дневное голосование).\n"
                    "• <b>🎲 Кости</b> — быстрая дуэль с таблицей рекордов по ID и @username."
                )
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [html5_btn],
                        [
                            InlineKeyboardButton(text="🐍 Змейка (чат)", callback_data="cmd:game:snake:start"),
                            InlineKeyboardButton(text="💣 Сапёр", callback_data="cmd:game:mines:start"),
                        ],
                        [
                            InlineKeyboardButton(text="🕵️ Мафия", callback_data="cmd:game:mafia:menu"),
                            InlineKeyboardButton(text="🎲 Кости", callback_data="cmd:game:duel"),
                        ],
                        [
                            InlineKeyboardButton(text="🏆 Рекорды", callback_data="cmd:game:top:dice_duel"),
                            InlineKeyboardButton(text="⬅️ Назад", callback_data="cmd:home"),
                        ],
                    ]
                )
            elif sub == "ai":
                text, kb = _build_grid_panel(
                    "🤖 <b>Выбери нейросеть для ответов:</b>",
                    AI_MODELS,
                    settings.get("ai_model", "gemini-4-hydrogen-high"),
                    "cmd:setai",
                )
            else:
                text, kb = _build_main_panel()
        else:
            text, kb = _build_main_panel()

        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
        await callback.answer()

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
            "Готово! Управляйте голосом, поведением, моделью ИИ и режимом через /commands."
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
        arg = (message.text or "").split(maxsplit=1)[1] if len((message.text or "").split(maxsplit=1)) > 1 else ""
        if arg.strip().lower() == "snake":
            u = message.from_user
            arcade_url = (
                f"https://rixed.store/arcade/?uid={u.id}"
                f"&uname={quote(u.username or '')}&dname={quote(u.full_name or '')}"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🚀 Запустить Змейку 60 FPS", web_app=WebAppInfo(url=arcade_url))],
                    [InlineKeyboardButton(text="🏆 Топ Змейки", callback_data="cmd:game:top:snake")],
                ]
            )
            await message.answer("🐍 <b>HTML5 Змейка готова к запуску!</b>", parse_mode="HTML", reply_markup=kb)
            return
        await message.answer(
            "Личка подключена. Настройки доступны по команде /commands."
        )

    @router.message(F.web_app_data)
    async def handle_webapp_data(message: Message) -> None:
        if message.from_user is None or message.web_app_data is None:
            return
        try:
            payload = json.loads(message.web_app_data.data)
            score = int(payload.get("score") or 0)
            game = str(payload.get("game") or "snake")
        except Exception:
            return
        if score <= 0:
            return
        try:
            from tg_bot import games as gmod
        except ImportError:
            import joke_bot_games as gmod
        u = message.from_user
        stats = gmod.record_game_score(
            user_id=u.id,
            username=u.username or "",
            display_name=u.full_name or str(u.id),
            game=game,
            points=score,
        )
        await message.answer(
            f"🐍 <b>Счёт сохранён!</b> +{score} очк. | Рекорд: <b>{stats['best_score']}</b>",
            parse_mode="HTML",
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
            text, kb = _build_main_panel()
            await message.answer(text, parse_mode="HTML", reply_markup=kb)

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
        await message.answer("Я снова в эфире 😎")

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

    @router.message(Command("generate"))
    async def generate(message: Message) -> None:
        configured = get_group()
        if not configured or message.chat.id != configured[0] or message.from_user is None:
            return
        raw = (message.text or message.caption or "").strip()
        parts = raw.split(maxsplit=1)
        image_prompt = parts[1].strip() if len(parts) == 2 else ""
        if not image_prompt:
            await message.answer("После /generate с новой строки напишите точный промпт изображения.")
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
        if is_group and is_muted():
            return

        enqueue(
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

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    arcade_runner = await _start_arcade_server()
    await bot.delete_webhook(drop_pending_updates=False)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await arcade_runner.cleanup()
        await bot.session.close()


async def _start_arcade_server() -> web.AppRunner:
    """Serve the <4KB HTML5 arcade mini-app and accept score submissions on 127.0.0.1:8120."""
    async def handle_index(_request: web.Request) -> web.Response:
        for cand in (
            Path("/opt/rixed-joke-bot/miniapp_games.html"),
            Path(__file__).with_name("miniapp_games.html"),
        ):
            if cand.is_file():
                return web.Response(text=cand.read_text(encoding="utf-8"), content_type="text/html")
        return web.Response(text="<h1>Arcade not found</h1>", status=404, content_type="text/html")

    async def handle_score(request: web.Request) -> web.Response:
        try:
            data = await request.json()
            uid = int(data.get("user_id") or 0)
            score = int(data.get("score") or 0)
            game = str(data.get("game") or "snake")[:24]
            uname = str(data.get("username") or "")[:64]
            dname = str(data.get("display_name") or uid)[:64]
        except Exception:
            return web.json_response({"ok": False}, status=400)
        if uid <= 0 or score <= 0:
            return web.json_response({"ok": False}, status=400)
        try:
            from tg_bot import games as gmod
        except ImportError:
            import joke_bot_games as gmod
        stats = gmod.record_game_score(user_id=uid, username=uname, display_name=dname, game=game, points=score)
        return web.json_response({"ok": True, **stats})

    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/index.html", handle_index)
    app.router.add_post("/api/score", handle_score)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", int(os.environ.get("ARCADE_PORT", "8120")))
    await site.start()
    LOGGER.info("Arcade server listening on 127.0.0.1:%s", os.environ.get("ARCADE_PORT", "8120"))
    return runner


if __name__ == "__main__":
    asyncio.run(main())
