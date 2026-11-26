#!/usr/bin/env python3
"""Autonomous Telegram message deletion tool for tg-bot."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import urllib.request

from tg_bot.config import REMOTE_PYTHON, REMOTE_QUEUE_SCRIPT, REMOTE_SSH_HOST, read_token

LOGGER = logging.getLogger("tg_bot.delete")


def remote_queue(*arguments: str) -> str:
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            REMOTE_SSH_HOST,
            REMOTE_PYTHON,
            REMOTE_QUEUE_SCRIPT,
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    return result.stdout.strip()


def record_bot_message(chat_id: int, message_id: int, task_id: int | None = None) -> None:
    """Record a sent bot message so it can be deleted later if requested."""
    try:
        args = ["track-bot-message", "--chat-id", str(chat_id), "--message-id", str(message_id)]
        if task_id:
            args.extend(["--task-id", str(task_id)])
        remote_queue(*args)
    except Exception as exc:
        LOGGER.warning("Could not track bot message %s: %s", message_id, exc)


def get_recorded_bot_messages(chat_id: int, limit: int = 100) -> list[int]:
    try:
        res = remote_queue("get-bot-messages", "--chat-id", str(chat_id), "--limit", str(limit))
        data = json.loads(res)
        return [int(mid) for mid in data.get("message_ids", [])]
    except Exception as exc:
        LOGGER.warning("Could not get tracked bot messages for chat %s: %s", chat_id, exc)
        return []


def clear_recorded_messages(chat_id: int, message_ids: list[int] | None = None) -> None:
    try:
        args = ["clear-bot-messages", "--chat-id", str(chat_id)]
        if message_ids:
            args.extend(["--message-ids", ",".join(str(m) for m in message_ids)])
        remote_queue(*args)
    except Exception as exc:
        LOGGER.warning("Could not clear tracked bot messages: %s", exc)


def delete_single_message(token: str, chat_id: int, message_id: int) -> bool:
    url = f"https://api.telegram.org/bot{token}/deleteMessage"
    data = json.dumps({"chat_id": chat_id, "message_id": message_id}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.load(resp)
            return bool(body.get("ok"))
    except Exception:
        return False


def delete_messages_bulk(token: str, chat_id: int, message_ids: list[int]) -> list[int]:
    """Delete messages in batches of up to 100 with single-delete fallback."""
    if not message_ids:
        return []
    deleted: list[int] = []
    for i in range(0, len(message_ids), 100):
        batch = message_ids[i : i + 100]
        url = f"https://api.telegram.org/bot{token}/deleteMessages"
        data = json.dumps({"chat_id": chat_id, "message_ids": batch}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        success = False
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.load(resp)
                if body.get("ok"):
                    deleted.extend(batch)
                    success = True
        except Exception:
            success = False
        if not success:
            for mid in batch:
                if delete_single_message(token, chat_id, mid):
                    deleted.append(mid)
    return deleted


def delete_bot_messages(
    chat_id: int,
    count: int | None = None,
    token: str | None = None,
) -> list[int]:
    tok = token or read_token()
    limit = count if count is not None and count > 0 else 100
    mids = get_recorded_bot_messages(chat_id, limit=limit)
    if count is not None and count > 0:
        mids = mids[:count]
    if not mids:
        return []
    deleted = delete_messages_bulk(tok, chat_id, mids)
    if deleted:
        clear_recorded_messages(chat_id, deleted)
    return deleted


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Delete recorded Telegram bot messages")
    parser.add_argument("--chat-id", type=int, required=True)
    parser.add_argument("--all-bot", action="store_true")
    parser.add_argument("--last", type=int, default=0)
    parser.add_argument("--message-ids", type=str, default="")
    args = parser.parse_args()

    token = read_token()
    if args.message_ids:
        mids = [int(m.strip()) for m in args.message_ids.split(",") if m.strip()]
        deleted = delete_messages_bulk(token, args.chat_id, mids)
        clear_recorded_messages(args.chat_id, deleted)
    elif args.all_bot:
        deleted = delete_bot_messages(args.chat_id, count=None, token=token)
    elif args.last > 0:
        deleted = delete_bot_messages(args.chat_id, count=args.last, token=token)
    else:
        print(json.dumps({"status": "error", "message": "Specify --all-bot, --last N, or --message-ids"}))
        return
    print(json.dumps({"status": "ok", "deleted_count": len(deleted), "deleted_ids": deleted}))


if __name__ == "__main__":
    main()
