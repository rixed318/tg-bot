#!/usr/bin/env python3
"""Send large files (>50 MB) through an explicitly authorized Telegram user session."""

from __future__ import annotations

import argparse
from pathlib import Path

STATE_DIR = Path.home() / ".local" / "share" / "rixed-joke-userbot"
ENV_FILE = STATE_DIR / "env"


def settings() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat-id", type=int, required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--reply-to", type=int)
    args = parser.parse_args()
    path = args.file.resolve()
    if not path.is_file() or path.stat().st_size > 4 * 1024**3:
        raise SystemExit("File is missing or exceeds 4 GiB")
    values = settings()
    session = values.get("TELEGRAM_SESSION") or str(STATE_DIR / "premium")
    from telethon.sync import TelegramClient

    with TelegramClient(
        session,
        int(values["TELEGRAM_API_ID"]),
        values["TELEGRAM_API_HASH"],
    ) as client:
        me = client.get_me()
        limit = 4 * 1024**3 if me.premium else 2 * 1024**3
        if path.stat().st_size > limit:
            raise SystemExit("Authorized account does not have the required Premium limit")
        message = client.send_file(
            args.chat_id,
            file=str(path),
            caption=path.name[:1024],
            force_document=True,
            reply_to=args.reply_to if args.chat_id < 0 else None,
        )
        print(message.id)


if __name__ == "__main__":
    main()
