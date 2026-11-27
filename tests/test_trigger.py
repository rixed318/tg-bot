from __future__ import annotations

from pathlib import Path

from tg_bot.trigger import (
    TriggerDecision,
    build_reply_prompt,
    is_explicit_call,
    parse_trigger_json,
)


def test_explicit_call_word_boundaries_prevent_false_positives() -> None:
    # Previously "ии", "бот", "сделай" triggered false positives inside Russian words!
    assert is_explicit_call({"chat_id": -100, "text": "В такой ситуации работа не идет"}) is False
    assert is_explicit_call({"chat_id": -100, "text": "Сделай музыку потише в наушниках"}) is False
    assert is_explicit_call({"chat_id": -100, "text": "Обычный чат про бота в доте"}) is False

    assert is_explicit_call({"chat_id": -100, "text": "@w_tau_swag_bot помоги"}, "w_tau_swag_bot") is True
    assert is_explicit_call({"chat_id": -100, "text": "Тау бао, подскажи по сабсёрфу"}) is True
    assert is_explicit_call({"chat_id": -100, "text": "Да", "reply_to_is_bot": True}) is True
    assert is_explicit_call({"chat_id": 12345, "text": "привет"}) is True


def test_parse_trigger_json_handles_fences_and_invalid_output() -> None:
    raw = """```json
    {"should_reply": true, "reason": "3 сообщения про рендер", "target_message_ids": [10, 11, 12, 999]}
    ```"""
    decision = parse_trigger_json(raw, [10, 11, 12])
    assert decision.should_reply is True
    assert decision.flag == "reply"
    assert decision.target_message_ids == (10, 11, 12)

    bad = parse_trigger_json("какой-то мусор без json", [10, 11])
    assert bad.should_reply is False
    assert bad.flag == "skip"


def test_build_reply_prompt_focuses_batch_and_enforces_voice_format(tmp_path: Path) -> None:
    unanswered = [
        {"message_id": 1, "sender_name": "Никита", "text": "Привет"},
        {"message_id": 2, "sender_name": "Серёжа", "text": "Блендер лагает"},
        {"message_id": 3, "sender_name": "Влад", "text": "Сколько сэмплов ставил?"},
    ]
    decision = TriggerDecision(
        should_reply=True,
        flag="reply",
        reason="накопились 3 реплики",
        target_message_ids=(1, 2, 3),
    )
    prompt_text = build_reply_prompt(
        {"chat_id": -100, "message_id": 3, "context": [{"message_id": 0, "author": "A", "text": "old"}]},
        decision=decision,
        unanswered=unanswered,
        attachment=None,
        context_attachments=(),
        work_dir=tmp_path,
    )
    assert "ИСКЛЮЧИТЕЛЬНО ГОЛОСОВЫМИ СООБЩЕНИЯМИ" in prompt_text
    assert "full-context.jsonl" in prompt_text
    assert "Блендер лагает" in prompt_text
    assert "Сколько сэмплов ставил?" in prompt_text


def test_persona_and_grok_response_extraction(tmp_path: Path) -> None:
    from tg_bot.llm_client import _extract_grok_response
    from tg_bot.personas import get_persona_prompt

    assert "Дед Геннадий" in get_persona_prompt("ded")
    assert "Демон" in get_persona_prompt("demon") or "демон" in get_persona_prompt("demon")
    assert _extract_grok_response('{"response": "ОК"}') == "ОК"

    p = build_reply_prompt(
        {
            "chat_id": -100,
            "message_id": 1,
            "context": [],
            "bot_settings": {"persona": "ded"},
        },
        decision=TriggerDecision(True, "reply", "тест", (1,)),
        unanswered=[{"message_id": 1, "sender_name": "А", "text": "Привет"}],
        attachment=None,
        context_attachments=(),
        work_dir=tmp_path,
    )
    assert "Дед Геннадий" in p


def test_strip_meta_thoughts() -> None:
    from tg_bot.llm_client import strip_meta_thoughts

    raw = (
        "Сначала гляну историю чата, чтобы ответить в том же тоне.\n"
        "Ну наконец-то без лишней болтовни."
    )
    assert strip_meta_thoughts(raw) == "Ну наконец-то без лишней болтовни."

    raw2 = (
        "Сначала тихо сверюсь с историей, кто такая Катя и как у них с голосами, "
        "потом сразу одна живая реплика. Ну вот, Катя на линии."
    )
    assert strip_meta_thoughts(raw2) == "Ну вот, Катя на линии."

    raw3 = "Сначала молча гляну, кто я в этом чате и про какой трек Рус орёт. Рус, трек огонь!"
    assert strip_meta_thoughts(raw3) == "Рус, трек огонь!"

    raw4 = (
        "Ищу трек в истории чата, чтобы зачитать его целиком. "
        "Ищу текст трека и связанные файлы, чтобы зачитать его целиком. "
        "Воспитала улица его."
    )
    assert strip_meta_thoughts(raw4) == "Воспитала улица его."


def test_explicit_call_does_not_merge_unrelated_prior_request(tmp_path: Path) -> None:
    from tg_bot.trigger import evaluate_trigger

    unanswered = [
        {"message_id": 8749, "sender_name": "Рус", "text": "Секси ксюшу сгенерируй, фото + видео"},
        {"message_id": 8753, "sender_name": "Рус", "text": "[К сообщению прикреплено: фотография]"},
        {"message_id": 8754, "sender_name": "Рус", "text": "Создай эмоджи пак из этих изображений"},
    ]
    decision = evaluate_trigger(
        {"chat_id": 12345, "message_id": 8754, "text": "Создай эмоджи пак из этих изображений"},
        tmp_path,
        unanswered=unanswered,
    )
    assert decision.should_reply is True
    assert decision.target_message_ids == (8753, 8754)


def test_strip_meta_thoughts_removes_grok_progress_paragraphs() -> None:
    from tg_bot.llm_client import strip_meta_thoughts

    raw = (
        "Генерирую кадр Ксюши, потом сразу склею ролик.\n\n"
        "Кадр с кроватью не прошёл, беру вечерний гламур.\n\n"
        "Кадр готов — копирую в исходящие и собираю ролик.\n\n"
        "Ролик подозрительно лёгкий — проверю, что монтаж реально собрался.\n\n"
        "Монтаж схлопнулся в один кадр — сначала сделаю нормальный ролик из фото.\n\n"
        "Соберу из фото восьмисекундный ролик с наездом, потом прогоню через монтаж.\n\n"
        "Рус, лови меня в чёрном платье на крыше — фото и видео, как заказывал."
    )
    assert strip_meta_thoughts(raw) == "Рус, лови меня в чёрном платье на крыше — фото и видео, как заказывал."
