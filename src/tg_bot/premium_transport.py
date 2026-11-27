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
    parser.add_argument("--file", type=Path)
    parser.add_argument("--html-file", type=Path)
    parser.add_argument("--video-note", action="store_true")
    parser.add_argument("--voice", action="store_true")
    parser.add_argument("--reply-to", type=int)
    args = parser.parse_args()
    if not args.file and not args.html_file:
        raise SystemExit("Provide --file or --html-file")
    values = settings()
    session = values.get("TELEGRAM_SESSION") or str(STATE_DIR / "premium")
    from telethon.sync import TelegramClient

    with TelegramClient(
        session,
        int(values["TELEGRAM_API_ID"]),
        values["TELEGRAM_API_HASH"],
    ) as client:
        me = client.get_me()
        reply_to = args.reply_to if args.chat_id < 0 else None
        if args.html_file:
            html_text = args.html_file.read_text(encoding="utf-8").strip()
            message = client.send_message(
                args.chat_id,
                html_text,
                parse_mode="html",
                reply_to=reply_to,
            )
            print(message.id)
            return
        path = args.file.resolve()
        if not path.is_file() or path.stat().st_size > 4 * 1024**3:
            raise SystemExit("File is missing or exceeds 4 GiB")
        limit = 4 * 1024**3 if me.premium else 2 * 1024**3
        if path.stat().st_size > limit:
            raise SystemExit("Authorized account does not have the required Premium limit")
        message = client.send_file(
            args.chat_id,
            file=str(path),
            caption="" if (args.video_note or args.voice) else path.name[:1024],
            force_document=not (args.video_note or args.voice),
            video_note=args.video_note,
            voice_note=args.voice,
            reply_to=reply_to,
        )
        print(message.id)


if __name__ == "__main__":
    main()
