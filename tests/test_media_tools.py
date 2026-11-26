from __future__ import annotations

from pathlib import Path

import pytest

from tg_bot.media_tools import parse_agent_response
from tg_bot.telegram_api import multipart_form_data


def test_parses_only_outbox_images(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    image = outbox / "meme.png"
    image.write_bytes(b"png")
    response = parse_agent_response("Держи\n[[telegram-photo:outbox/meme.png]]", tmp_path)
    assert response.text == "Держи"
    assert response.photos == (image,)
    with pytest.raises(ValueError):
        parse_agent_response("[[telegram-photo:../secret.png]]", tmp_path)


def test_multipart_contains_voice_field(tmp_path: Path) -> None:
    ogg = tmp_path / "reply.ogg"
    ogg.write_bytes(b"OggS")
    body, boundary = multipart_form_data({"chat_id": "-100"}, "voice", ogg)
    assert boundary.encode() in body
    assert b'name="voice"' in body
    assert b"OggS" in body
