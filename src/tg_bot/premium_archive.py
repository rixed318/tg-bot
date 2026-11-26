#!/usr/bin/env python3
"""Passively archive media from the configured Telegram group to Google Drive."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import subprocess
import tempfile

from telethon import TelegramClient, events

STATE_DIR = Path.home() / ".local" / "share" / "rixed-joke-userbot"
ENV_FILE = STATE_DIR / "env"
GROUP_ID = -1003800116774
DRIVE_ROOT = "gdrive:Тау бао — медиа"


def settings() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


async def main() -> None:
    values = settings()
    session = values.get("TELEGRAM_SESSION") or str(STATE_DIR / "premium")
    client = TelegramClient(
        session,
        int(values["TELEGRAM_API_ID"]),
        values["TELEGRAM_API_HASH"],
    )

    @client.on(events.NewMessage(chats=GROUP_ID))
    async def archive(event: events.NewMessage.Event) -> None:
        if not event.message.file:
            return
        with tempfile.TemporaryDirectory(prefix="tau-media-") as directory:
            downloaded = await event.message.download_media(file=directory)
            if not downloaded:
                return
            path = Path(downloaded)
            month = datetime.now(UTC).strftime("%Y-%m")
            suffix = path.suffix.lower() or ".bin"
            destination = (
                f"{DRIVE_ROOT}/group-{abs(GROUP_ID)}/{month}/incoming/"
                f"message-{event.message.id}{suffix}"
            )
            await asyncio.to_thread(
                subprocess.run,
                [
                    "/home/rus/.local/bin/rclone",
                    "copyto",
                    str(path),
                    destination,
                    "--drive-chunk-size",
                    "64M",
                    "--retries",
                    "3",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=7200,
            )

    await client.start()
    me = await client.get_me()
    if not me.premium:
        raise RuntimeError("Configured Telegram user session is not Premium")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
