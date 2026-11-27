"""Explicit-call detection, stateful trigger classifier, and consolidated reply prompts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from tg_bot.config import (
    GROUP_COOLDOWN_SECONDS,
    INLINE_HISTORY_LIMIT,
    TRIGGER_MODEL,
    read_bot_username,
)
from tg_bot.llm_client import run_llm_prompt
from tg_bot.personas import get_persona_prompt

# Strict word-boundary bot name triggers (avoid substring false-positives like "ситуации" or "работа").
BOT_NAME_PATTERN = re.compile(
    r"(?<![а-яёa-z0-9_])(?:тау(?:\s+бао)?|таубао|tau(?:\s+bao)?|ботяр[а-яё]*)(?![а-яёa-z0-9_])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TriggerDecision:
    should_reply: bool
    flag: str  # "reply" | "skip"
    reason: str
    target_message_ids: tuple[int, ...]
    explicit: bool = False


def is_explicit_call(task: dict[str, Any], bot_username: str | None = None) -> bool:
    """Return True only when the message explicitly targets the bot (DM, reply, @mention, or name)."""
    chat_id = int(task.get("chat_id") or 0)
    if chat_id > 0:
        return True

    if task.get("reply_to_is_bot") or task.get("is_mention"):
        return True

    for bt in task.get("bundled_tasks") or []:
        if bt.get("reply_to_is_bot") or bt.get("is_mention"):
            return True

    raw_text = str(task.get("text") or "")
    if raw_text.startswith("Сгенерируй изображение строго по этому промпту:"):
        return True

    texts = [raw_text] + [str(bt.get("text") or "") for bt in task.get("bundled_tasks") or []]
    combined = " ".join(texts)

    uname = (bot_username or read_bot_username() or "w_tau_swag_bot").lstrip("@").lower()
    if uname and f"@{uname}" in combined.lower():
        return True

    if BOT_NAME_PATTERN.search(combined):
        return True

    return False


def seconds_since_last_bot_reply(task: dict[str, Any]) -> float | None:
    """Compute seconds elapsed since the bot's last non-skip reply in this chat."""
    raw_ts = task.get("last_bot_reply_at")
    if not raw_ts:
        return None
    try:
        dt = datetime.fromisoformat(str(raw_ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - dt).total_seconds())
    except Exception:
        return None


def collect_unanswered_messages(task: dict[str, Any]) -> list[dict[str, Any]]:
    """Build ordered list of unanswered messages since the bot's last reply."""
    unanswered = list(task.get("unanswered_messages") or [])
    if unanswered:
        # Ensure primary task's latest text (including fresh voice transcription) is present.
        primary_id = int(task.get("message_id") or 0)
        found = False
        for item in unanswered:
            if int(item.get("message_id") or 0) == primary_id:
                item["text"] = task.get("text", item.get("text", ""))
                found = True
        if not found:
            unanswered.append(
                {
                    "task_id": task.get("id"),
                    "message_id": primary_id,
                    "sender_id": task.get("sender_id"),
                    "sender_name": task.get("sender_name"),
                    "text": task.get("text", ""),
                    "photo_file_id": task.get("photo_file_id"),
                    "reply_to_message_id": task.get("reply_to_message_id"),
                    "reply_to_is_bot": bool(task.get("reply_to_is_bot")),
                    "is_mention": bool(task.get("is_mention")),
                    "created_at": task.get("created_at"),
                }
            )
        return unanswered

    items = [
        {
            "task_id": bt.get("id"),
            "message_id": bt.get("message_id"),
            "sender_id": bt.get("sender_id"),
            "sender_name": bt.get("sender_name"),
            "text": bt.get("text", ""),
            "photo_file_id": bt.get("photo_file_id"),
            "reply_to_message_id": bt.get("reply_to_message_id"),
            "reply_to_is_bot": bool(bt.get("reply_to_is_bot")),
            "is_mention": bool(bt.get("is_mention")),
            "created_at": bt.get("created_at"),
        }
        for bt in (task.get("bundled_tasks") or [])
    ]
    items.append(
        {
            "task_id": task.get("id"),
            "message_id": task.get("message_id"),
            "sender_id": task.get("sender_id"),
            "sender_name": task.get("sender_name"),
            "text": task.get("text", ""),
            "photo_file_id": task.get("photo_file_id"),
            "reply_to_message_id": task.get("reply_to_message_id"),
            "reply_to_is_bot": bool(task.get("reply_to_is_bot")),
            "is_mention": bool(task.get("is_mention")),
            "created_at": task.get("created_at"),
        }
    )
    return items


def _format_recent_context(task: dict[str, Any], limit: int = INLINE_HISTORY_LIMIT) -> str:
    history = list(task.get("context") or [])
    if not history:
        return "(история пока пуста)"
    recent = history[-limit:]
    lines = []
    for row in recent:
        mid = row.get("message_id", "?")
        author = row.get("author", "?")
        text = str(row.get("text") or "").replace("\n", " ")[:400]
        flag = f" [flag={row['decision_flag']}]" if row.get("decision_flag") else ""
        lines.append(f"[{mid}] {author}{flag}: {text}")
    return "\n".join(lines)


def _format_unanswered(unanswered: list[dict[str, Any]]) -> str:
    lines = []
    for item in unanswered:
        mid = item.get("message_id", "?")
        author = item.get("sender_name", "?")
        text = str(item.get("text") or "").replace("\n", " ")[:500]
        prev_flag = f" (ранее: {item['decision_flag']})" if item.get("decision_flag") else ""
        lines.append(f"[msg_id={mid}] {author}{prev_flag}: {text}")
    return "\n".join(lines)


def parse_trigger_json(raw_response: str, available_ids: list[int]) -> TriggerDecision:
    """Parse structured JSON flag returned by the trigger evaluator."""
    cleaned = raw_response.strip()
    # Extract JSON object even if wrapped in markdown fences
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return TriggerDecision(
            should_reply=False,
            flag="skip",
            reason="модель не вернула JSON-флаг, безопасный пропуск",
            target_message_ids=(),
            explicit=False,
        )
    try:
        data = json.loads(match.group(0))
    except Exception:
        return TriggerDecision(
            should_reply=False,
            flag="skip",
            reason="невалидный JSON триггера, безопасный пропуск",
            target_message_ids=(),
            explicit=False,
        )

    should_reply = bool(data.get("should_reply"))
    reason = str(data.get("reason") or "").strip()[:300] or (
        "решено ответить" if should_reply else "обычный чат без повода влезать"
    )
    raw_targets = data.get("target_message_ids") or []
    available_set = set(available_ids)
    targets = tuple(
        int(mid) for mid in raw_targets if str(mid).isdigit() and int(mid) in available_set
    )
    if should_reply and not targets and available_ids:
        targets = tuple(available_ids[-3:])
    return TriggerDecision(
        should_reply=should_reply,
        flag="reply" if should_reply else "skip",
        reason=reason,
        target_message_ids=targets,
        explicit=False,
    )


def evaluate_trigger(
    task: dict[str, Any],
    work_dir: Path,
    *,
    unanswered: list[dict[str, Any]],
    cooldown_seconds: float = GROUP_COOLDOWN_SECONDS,
) -> TriggerDecision:
    """Read incoming messages and have the model set its own reply/skip flag."""
    available_ids = [int(item["message_id"]) for item in unanswered if item.get("message_id")]

    if is_explicit_call(task):
        # If there are 2-3 recent unanswered messages right before/with the explicit call,
        # keep recent context cohesive by targeting up to last 3 unanswered messages.
        targets = tuple(available_ids[-3:]) if available_ids else (int(task["message_id"]),)
        return TriggerDecision(
            should_reply=True,
            flag="reply",
            reason="прямое обращение к боту или личный чат",
            target_message_ids=targets,
            explicit=True,
        )

    elapsed = seconds_since_last_bot_reply(task)
    if elapsed is not None and elapsed < cooldown_seconds:
        return TriggerDecision(
            should_reply=False,
            flag="skip",
            reason=f"кулдаун после прошлого ГС ({elapsed:.0f}с < {cooldown_seconds:.0f}с)",
            target_message_ids=(),
            explicit=False,
        )

    recent_history = _format_recent_context(task, limit=15)
    unanswered_block = _format_unanswered(unanswered)
    elapsed_note = f"{elapsed:.0f} секунд назад" if elapsed is not None else "давно"

    classifier_prompt = f"""Ты — модуль-триггер в Telegram-группе «Блендер» (участники общаются по 3D/Blender и обо всём подряд).
Ты читаешь КАЖДОЕ сообщение, но отвечаешь ТОЛЬКО голосовым и ТОЛЬКО тогда, когда действительно стоит вклиниться.

ТВОЯ ЗАДАЧА — поставить самому себе флаг `"should_reply": true` или `false`.
ПРАВИЛА БАЛАНСА (без жёсткого антиспама):
1. Пропускай (`"should_reply": false`) примерно 35–45% совсем уж пустых или чисто личных реплик: одиночные междометия («да», «ок», «лол», «хз»), короткие перебросы двоих участников строго друг с другом, когда нечего добавить.
2. Ставь `"should_reply": true`, если:
   - есть вопрос, тема (особенно Blender, 3D, ИИ, техника, жизнь группы), подкол или реакция на твоё прошлое ГС/кружочек (даже без формального reply);
   - или накопилось 2+ неотвеченных сообщений подряд — отличный повод ответить одним живым общим ГС/кружочком и прокомментировать их сразу в `target_message_ids`;
   - или кто-то просит действие (стикер, картинку, монтаж, гифку, удаление).

Время с твоего последнего ГС в группе: {elapsed_note}.
Всего в истории переписки (в `full-context.jsonl`): {len(task.get("context") or [])} сообщений.

НЕДАВНИЙ КОНТЕКСТ:
{recent_history}

НЕОТВЕЧЕННЫЕ СООБЩЕНИЯ ПОСЛЕ ТВОЕГО ПОСЛЕДНЕГО ГС:
{unanswered_block}

Верни СТРОГО один JSON-объект без пояснений вокруг:
{{"should_reply": true или false, "reason": "краткая причина на русском", "target_message_ids": [список msg_id из неотвеченных, которые стоит объединить в одном ГС, либо [] если should_reply=false]}}"""

    bot_settings = task.get("bot_settings") or {}
    ai_model = str(bot_settings.get("ai_model") or "").strip()
    trigger_model = ai_model if ai_model.startswith("grok") else TRIGGER_MODEL
    raw_out = run_llm_prompt(
        classifier_prompt,
        work_dir=work_dir,
        model_id=trigger_model,
        timeout_seconds=90,
    )
    return parse_trigger_json(raw_out, available_ids)


def build_reply_prompt(
    task: dict[str, Any],
    *,
    decision: TriggerDecision,
    unanswered: list[dict[str, Any]],
    attachment: Path | None,
    context_attachments: tuple[Path, ...],
    work_dir: Path,
) -> str:
    """Build the spoken-response prompt covering the triggered batch + unlimited history."""
    target_set = set(decision.target_message_ids)
    focus_items = [
        item for item in unanswered if int(item.get("message_id") or 0) in target_set
    ] or unanswered[-3:]

    focus_lines = []
    for item in focus_items:
        mid = item.get("message_id", "?")
        author = item.get("sender_name", "?")
        text = str(item.get("text") or "").strip()
        focus_lines.append(f"- [{mid}] {author}: {text}")
    focus_block = "\n".join(focus_lines)

    history_total = len(task.get("context") or [])
    recent_history = _format_recent_context(task, limit=INLINE_HISTORY_LIMIT)
    reply_context = task.get("reply_context") or "нет"

    attachment_note = (
        f"К последнему сообщению прикреплён файл `{attachment.name}` в текущей папке."
        if attachment
        else "К текущему сообщению нет прикреплённых файлов."
    )
    context_media_note = (
        "Доступные медиа из недавнего контекста: "
        + ", ".join(path.name for path in context_attachments)
        + "."
        if context_attachments
        else "Недавних медиа-вложений из контекста нет."
    )
    chat_kind = "личный диалог" if int(task.get("chat_id") or 0) > 0 else "группа «Блендер»"
    bot_settings = task.get("bot_settings") or {}
    persona_id = str(bot_settings.get("persona") or "default")
    persona_desc = get_persona_prompt(persona_id)

    return f"""{persona_desc}
Ты общаешься в Telegram ({chat_kind}).
ВАЖНЕЙШЕЕ ПРАВИЛО ФОРМАТА:
Ты общаешься ИСКЛЮЧИТЕЛЬНО ГОЛОСОВЫМИ СООБЩЕНИЯМИ! Весь твой текстовый ответ будет прочитан вслух нейросетью-озвучкой.
Поэтому:
- Пиши КАК ЖИВОЙ ЧЕЛОВЕК В ГОЛОСОВОМ СООБЩЕНИИ: разговорный русский язык, естественные интонации, короткие понятные фразы (обычно 1–4 предложения, без воды).
- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО проговаривать мысли вслух, планы или комментарии своих действий (никаких «Сначала гляну историю чата», «Сейчас проверю», «Посмотрю контекст» и т.п.). Вызывай инструменты молча, а в ответе выдавай ТОЛЬКО прямую реплику персонажа.
- ЗАПРЕЩЕНО использовать Markdown (`**`, `*`, `` ` ``, `#`, списки `- ` / `1.`), эмодзи, ASCII-смайлы и длинные URL!
- Иностранные термины (Blender, Cycles, Eevee, Render, UV, mesh, GPU) можешь писать по-русски или обычно — но избегай скобок с пояснениями и таблиц.
- Если в фокусе несколько сообщений сразу (например 2–4 реплики участников) — НЕ отвечай на каждое по очереди списком! Собери ОДНУ цельную, естественную голосовую реплику, затрагивающую суть этих сообщений.

ПАМЯТЬ (НЕОГРАНИЧЕННЫЙ КОНТЕКСТ):
- В твоей рабочей папке `{work_dir}` лежит файл `full-context.jsonl` со ВСЕЙ полной историей чата с самого первого дня ({history_total} записей, также синхронизируется на Google Диск).
- Если участники упоминают прошлые события, старые шутки, проекты или споры — обязательно проверь `full-context.jsonl` (через `read_file` или `rg`).

ИНСТРУМЕНТЫ И ДЕЙСТВИЯ НА ПК (если прямо попросили сделать файл/удаление):
- Папка вывода медиа: `{work_dir}/outbox/` (любое фото, стикер `.webp`/`.webm`, гифка `anim.mp4` или монтаж `.mp4` из `outbox/` отправятся автоматически).
- Монтаж 9:16: `/home/rus/dev/rixed-music/.venv/bin/python /home/rus/dev/rixed-music/scripts/telegram_bot_montage.py <input> <output> [hook] [--style romantic|phonk|cyber|auto]`
- Стикер/GIF: `/home/rus/dev/rixed-music/.venv/bin/python /home/rus/dev/rixed-music/scripts/telegram_bot_sticker_gif.py <input> <output> [--mode sticker|video_sticker|gif]`
- Вырезка фона: `/home/rus/dev/rixed-music/.venv/bin/python /home/rus/dev/rixed-music/scripts/telegram_bot_cutout.py <input> <output>`
- Удаление своих сообщений: директива `[[delete-all-bot-messages]]` или `[[delete-previous-message]]` (на отдельной строке, в озвучку не попадёт).
- Стикеры, эмодзи и стикерпаки: `[[make-sticker]]` (или `[[make-sticker:00:05-00:08]]` для видео), `[[make-emoji]]` (или `[[make-emoji:00:05-00:08]]`). Если пользователь просит создать **целый стикерпак или эмодзипак** с названием — используй `[[sticker-pack:Название пака]]` или `[[emoji-pack:Название пака]]` (бот сам соберёт все присланные фото/видео, подпишет пак и пришлёт ссылку `t.me/addstickers/...`)! Важно: видео-стикеры/эмодзи в Telegram не могут быть длиннее 3 секунд — если прислали длинное видео (>4 сек) и не указали таймкоды, сначала спроси 3-секундный диапазон (например `00:05-00:08`).
- Спеть / перепеть / зачитать трек под минус (кавер голосом активного персонажа поверх музыки): директива `[[singing-cover]]` или `[[singing-cover:<message_id>]]` (на отдельной строке, в озвучку не попадёт). Используй её ВСЕГДА, когда пользователь просит спеть, перепеть, зачитать трек, сделать кавер или наложить голос на минус/аудио из чата (укажи `<message_id>` того сообщения с треком, на которое ответил пользователь или которое он имеет в виду).
- Rich-форматированный текстовый ответ (вместо ГС/кружочка): ты САМ решаешь, когда лучше ответить текстом, даже если включены кружочки! Если пользователь просит расписать подробно, сделать гайд, статью, код, таблицу (`<pre>`), сворачиваемые блоки (`<blockquote expandable>`), цитаты или просит именно текст — обязательно добавь `[[rich-text]]` на отдельной строке и пиши красивый структурированный Telegram HTML (`<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<blockquote>`, `<blockquote expandable>`, `<tg-spoiler>`, `<a href="...">`).
- Камера в кружочке (опционально на отдельной строке, в озвучку не попадёт): `[[camera:pan_up]]`, `[[camera:pan_down]]`, `[[camera:pan_left]]`, `[[camera:pan_right]]`, `[[camera:zoom_in]]`, `[[camera:zoom_out]]`, `[[camera:tilt_left]]`, `[[camera:tilt_right]]`, `[[camera:nod]]`, `[[camera:shake]]` или `[[camera:auto]]` — двигай камеру и наклоняй голову, когда это уместно по настроению!
- Память персонажей разделена: прошлые ответы в истории могут быть помечены `Тау бао (как <другой_персонаж>)`. Если там указан другой персонаж (не твой текущий образ), это говорил НЕ ты — не приписывай чужие реплики и воспоминания себе!
- Эмоция аватара (опционально на отдельной строке в самом конце, в озвучку не попадёт): `[[emotion:neutral]]`, `[[emotion:happy]]`, `[[emotion:angry]]`, `[[emotion:laugh]]`, `[[emotion:shock]]` или `[[emotion:sad]]`.
- Генерация картинки: вызови свой встроенный инструмент генерации (`image_gen` в Grok/Codex или подагент `image-generator` в Gemini) и ОБЯЗАТЕЛЬНО скопируй полученный файл в `{work_dir}/outbox/generated.jpg` (или `.png`) через `cp`, либо используй `[[image-prompt:...]]`. Всё, что попадёт в `{work_dir}/outbox/`, отправится в чат автоматически!
- Если пользователь просит одновременно сгенерировать фото и видео из него: сначала сгенерируй картинку через `image_gen` -> `{work_dir}/outbox/generated.jpg`, а затем при желании сделай из неё стильный ролик через `telegram_bot_montage.py {work_dir}/outbox/generated.jpg {work_dir}/outbox/video.mp4`!
- Скачивание по ссылке: `[[download-url:https://...]]` или `[[download-url-to-group:https://...]]`.
- Отправить текущий файл из ЛС в группу: `[[copy-current-message-to-group]]`.
- Не грепай код самого бота. Выполни действие (если просили) и сразу выдай финальный текст для озвучки в ГС!

ПОСЛЕДНЯЯ ИСТОРИЯ ЧАТА (последние сообщения; полная история — в `full-context.jsonl`):
{recent_history}

КОНТЕКСТ REPLY: {reply_context}
ПРИЧИНА СРАБАТЫВАНИЯ ТРИГГЕРА: {decision.reason}

СООБЩЕНИЯ, НА КОТОРЫЕ ТЫ СЕЙЧАС ОТВЕЧАЕШЬ ОДНИМ ГОЛОСОВЫМ:
{focus_block}

{attachment_note}
{context_media_note}

ОТВЕТ (чистая разговорная русская речь для озвучки в одном голосовом сообщении, без эмодзи и markdown):"""
