#!/usr/bin/env python3
"""Secure one-time setup for the Telegram voice-first Gemini bot."""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

TOKEN_PATTERN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{20,}$")
LOCAL_ENV = Path.home() / ".config" / "rixed-joke-bot" / "env"


def verify(token: str) -> dict[str, object]:
    with urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/getMe", timeout=20
    ) as response:
        payload = json.load(response)
    if not payload.get("ok"):
        raise RuntimeError("Telegram отклонил токен")
    return payload["result"]


def main() -> None:
    token = getpass.getpass("Токен бота от BotFather (ввод скрыт): ").strip()
    if not TOKEN_PATTERN.fullmatch(token):
        raise SystemExit("Токен пустой или имеет неверный формат")
    bot = verify(token)
    content = f"BOT_TOKEN={token}\nBOT_USERNAME={bot.get('username', '')}\n"

    LOCAL_ENV.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(LOCAL_ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(content)
    os.chmod(LOCAL_ENV, 0o600)

    subprocess.run(
        [
            "ssh",
            "rixed",
            "install -d -m 0750 -o root -g root /etc/rixed-joke-bot && "
            "umask 077 && cat > /etc/rixed-joke-bot/env && "
            "chmod 600 /etc/rixed-joke-bot/env && "
            "systemctl enable --now rixed-joke-bot.service",
        ],
        input=content,
        text=True,
        check=True,
    )
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", "rixed-joke-gemini.service"], check=True
    )
    print(f"\nГотово: @{bot.get('username')} проверен и службы запущены.")


if __name__ == "__main__":
    main()
