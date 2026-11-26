from __future__ import annotations

from tg_bot import gateway


def test_main_panel_shows_quality_speech_display(monkeypatch) -> None:
    monkeypatch.setattr(
        gateway,
        "get_bot_settings",
        lambda: {"speech_display": "quality", "avatar_enabled": "1"},
    )

    text, keyboard = gateway._build_main_panel()

    assert "Отображение речи: <b>✨ Обычный (качественный)</b>" in text
    display_button = keyboard.inline_keyboard[1][0]
    assert display_button.callback_data == "cmd:speech_display:funny"


def test_main_panel_shows_funny_speech_display(monkeypatch) -> None:
    monkeypatch.setattr(
        gateway,
        "get_bot_settings",
        lambda: {"speech_display": "funny", "avatar_enabled": "1"},
    )

    text, keyboard = gateway._build_main_panel()

    assert "Отображение речи: <b>🤪 Ржачный</b>" in text
    display_button = keyboard.inline_keyboard[1][0]
    assert display_button.callback_data == "cmd:speech_display:quality"
