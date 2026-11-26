#!/usr/bin/env python3
"""Durable SQLite queue, unlimited context store, and trigger-flag state.

Designed to stay dependency-free (stdlib only) so it can run both inside the
local worker package and standalone on the VPS gateway.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any

DEFAULT_DATABASE = Path("/var/lib/rixed-joke-bot/queue.db")


def now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


def _ensure_column(
    database: sqlite3.Connection, table: str, column: str, col_def: str
) -> None:
    existing = {
        row["name"] for row in database.execute(f"pragma table_info({table})").fetchall()
    }
    if column not in existing:
        database.execute(f"alter table {table} add column {column} {col_def}")


@contextmanager
def connect(path: Path = DEFAULT_DATABASE) -> Iterator[sqlite3.Connection]:
    """Open, migrate, commit/rollback, and deterministically close SQLite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path, timeout=30.0)
    database.row_factory = sqlite3.Row
    try:
        database.execute("pragma journal_mode = wal")
        database.execute("pragma busy_timeout = 10000")
        database.executescript(
            """
            create table if not exists settings (
                key text primary key,
                value text not null
            );
            create table if not exists tasks (
                id integer primary key autoincrement,
                chat_id integer not null,
                thread_id integer,
                message_id integer not null,
                reply_to_message_id integer,
                reply_to_is_bot integer not null default 0,
                is_mention integer not null default 0,
                sender_id integer not null,
                sender_name text not null,
                text text not null,
                reply_context text,
                photo_file_id text,
                status text not null default 'queued',
                attempts integer not null default 0,
                created_at text not null,
                claimed_at text,
                completed_at text,
                response text,
                decision_flag text,
                decision_reason text,
                covered_by_task_id integer,
                progress_message_id integer,
                error text,
                unique(chat_id, message_id)
            );
            create table if not exists participants (
                user_id integer primary key,
                username text,
                display_name text not null,
                can_dm integer not null default 0,
                last_seen_at text not null
            );
            create table if not exists observations (
                chat_id integer not null,
                message_id integer not null,
                sender_id integer not null,
                sender_name text not null,
                text text not null,
                photo_file_id text,
                reply_to_message_id integer,
                created_at text not null,
                primary key(chat_id, message_id)
            );
            create table if not exists bot_messages (
                id integer primary key autoincrement,
                chat_id integer not null,
                message_id integer not null,
                task_id integer,
                created_at text not null,
                unique(chat_id, message_id)
            );
            create table if not exists chat_state (
                chat_id integer primary key,
                last_answered_message_id integer not null default 0,
                last_bot_reply_at text
            );
            """
        )
        _ensure_column(database, "tasks", "photo_file_id", "text")
        _ensure_column(database, "tasks", "progress_message_id", "integer")
        _ensure_column(database, "tasks", "reply_to_is_bot", "integer not null default 0")
        _ensure_column(database, "tasks", "is_mention", "integer not null default 0")
        _ensure_column(database, "tasks", "decision_flag", "text")
        _ensure_column(database, "tasks", "decision_reason", "text")
        _ensure_column(database, "tasks", "covered_by_task_id", "integer")
        _ensure_column(database, "observations", "reply_to_message_id", "integer")

        database.execute(
            """
            insert or ignore into observations(
                chat_id, message_id, sender_id, sender_name, text, photo_file_id, created_at
            )
            select chat_id, message_id, sender_id, sender_name, text, photo_file_id, created_at
            from tasks
            """
        )
        database.execute(
            """
            insert or ignore into bot_messages(chat_id, message_id, task_id, created_at)
            select chat_id, progress_message_id, id, coalesce(completed_at, created_at)
            from tasks
            where progress_message_id is not null
            """
        )
        # Seed chat_state.last_answered_message_id from historical non-skip replies
        # so pre-upgrade skipped/failed tasks do not suddenly look unanswered.
        database.execute(
            """
            insert or ignore into chat_state(chat_id, last_answered_message_id, last_bot_reply_at)
            select chat_id,
                   coalesce(max(case
                       when status in ('completed', 'failed')
                            and response is not null
                            and response != ''
                            and response not in ('[[skip]]', '[[muted]]')
                       then message_id
                       else 0
                   end), 0),
                   max(case
                       when status = 'completed'
                            and response is not null
                            and response not in ('[[skip]]', '[[muted]]', '')
                       then coalesce(completed_at, created_at)
                       else null
                   end)
            from tasks
            group by chat_id
            """
        )
        database.commit()
        yield database
        database.commit()
    except Exception:
        database.rollback()
        raise
    finally:
        database.close()


def set_group(chat_id: int, title: str, path: Path = DEFAULT_DATABASE) -> None:
    with connect(path) as database:
        database.executemany(
            "insert or replace into settings(key, value) values(?, ?)",
            (("chat_id", str(chat_id)), ("chat_title", title), ("muted", "0")),
        )


def set_muted(muted: bool, path: Path = DEFAULT_DATABASE) -> None:
    with connect(path) as database:
        database.execute(
            "insert or replace into settings(key, value) values('muted', ?)",
            ("1" if muted else "0",),
        )


def is_muted(path: Path = DEFAULT_DATABASE) -> bool:
    with connect(path) as database:
        value = database.execute(
            "select value from settings where key='muted'"
        ).fetchone()
    return bool(value and value["value"] == "1")


def set_bot_setting(key: str, value: str, path: Path = DEFAULT_DATABASE) -> None:
    with connect(path) as database:
        database.execute(
            "insert or replace into settings(key, value) values(?, ?)",
            (key, value),
        )


def get_bot_settings(path: Path = DEFAULT_DATABASE) -> dict[str, str]:
    with connect(path) as database:
        rows = database.execute("select key, value from settings").fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def remember_participant(
    user_id: int,
    username: str | None,
    display_name: str,
    *,
    can_dm: bool = False,
    path: Path = DEFAULT_DATABASE,
) -> None:
    with connect(path) as database:
        database.execute(
            """
            insert into participants(user_id, username, display_name, can_dm, last_seen_at)
            values (?, ?, ?, ?, ?)
            on conflict(user_id) do update set
                username=excluded.username,
                display_name=excluded.display_name,
                can_dm=max(participants.can_dm, excluded.can_dm),
                last_seen_at=excluded.last_seen_at
            """,
            (user_id, username, display_name, int(can_dm), now_iso()),
        )


def get_group(path: Path = DEFAULT_DATABASE) -> tuple[int, str] | None:
    with connect(path) as database:
        rows = dict(database.execute("select key, value from settings").fetchall())
    if "chat_id" not in rows:
        return None
    return int(rows["chat_id"]), rows.get("chat_title", "Telegram-группа")


def remember_message(
    *,
    chat_id: int,
    message_id: int,
    sender_id: int,
    sender_name: str,
    text: str,
    photo_file_id: str | None = None,
    reply_to_message_id: int | None = None,
    path: Path = DEFAULT_DATABASE,
) -> None:
    with connect(path) as database:
        database.execute(
            """
            insert or ignore into observations(
                chat_id, message_id, sender_id, sender_name, text,
                photo_file_id, reply_to_message_id, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                message_id,
                sender_id,
                sender_name,
                text,
                photo_file_id,
                reply_to_message_id,
                now_iso(),
            ),
        )


def update_observation_text(
    *,
    chat_id: int,
    message_id: int,
    text: str,
    path: Path = DEFAULT_DATABASE,
) -> None:
    """Persist enriched text (e.g. voice transcription) in observations and tasks."""
    with connect(path) as database:
        database.execute(
            "update observations set text = ? where chat_id = ? and message_id = ?",
            (text, chat_id, message_id),
        )
        database.execute(
            "update tasks set text = ? where chat_id = ? and message_id = ?",
            (text, chat_id, message_id),
        )


def enqueue(
    *,
    chat_id: int,
    thread_id: int | None,
    message_id: int,
    reply_to_message_id: int | None,
    sender_id: int,
    sender_name: str,
    text: str,
    reply_context: str | None,
    photo_file_id: str | None = None,
    reply_to_is_bot: bool = False,
    is_mention: bool = False,
    path: Path = DEFAULT_DATABASE,
) -> bool:
    with connect(path) as database:
        cursor = database.execute(
            """
            insert or ignore into tasks(
                chat_id, thread_id, message_id, reply_to_message_id,
                reply_to_is_bot, is_mention,
                sender_id, sender_name, text, reply_context, photo_file_id, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                thread_id,
                message_id,
                reply_to_message_id,
                int(reply_to_is_bot),
                int(is_mention),
                sender_id,
                sender_name,
                text,
                reply_context,
                photo_file_id,
                now_iso(),
            ),
        )
        return cursor.rowcount == 1


def _context_rows(
    database: sqlite3.Connection,
    chat_id: int,
    *,
    up_to_message_id: int | None,
    source: str,
) -> list[dict[str, Any]]:
    """Return UNLIMITED full chronological context for chat_id up to message_id."""
    clause = "and o.message_id <= ?" if up_to_message_id is not None else ""
    parameters: tuple[Any, ...] = (
        (chat_id, up_to_message_id) if up_to_message_id is not None else (chat_id,)
    )
    rows = database.execute(
        f"""
        select o.message_id, o.sender_id, o.sender_name, o.text, o.photo_file_id,
               o.reply_to_message_id, o.created_at,
               t.response, t.decision_flag, t.decision_reason, t.completed_at
        from observations o
        left join tasks t on t.chat_id=o.chat_id and t.message_id=o.message_id
        where o.chat_id = ? {clause}
        order by o.message_id asc
        """,
        parameters,
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {
            "message_id": row["message_id"],
            "sender_id": row["sender_id"],
            "author": row["sender_name"],
            "text": row["text"],
            "source": source,
            "created_at": row["created_at"],
        }
        if row["reply_to_message_id"]:
            item["reply_to_message_id"] = row["reply_to_message_id"]
        if row["photo_file_id"]:
            item["photo_file_id"] = row["photo_file_id"]
        if row["decision_flag"]:
            item["decision_flag"] = row["decision_flag"]
        if row["decision_reason"]:
            item["decision_reason"] = row["decision_reason"]
        result.append(item)
        if row["response"] and row["response"] not in {"[[skip]]", "[[muted]]"}:
            result.append(
                {
                    "message_id": row["message_id"],
                    "author": "Тау бао",
                    "text": row["response"],
                    "source": source,
                    "created_at": row["completed_at"] or row["created_at"],
                }
            )
    return result


def _unanswered_rows(
    database: sqlite3.Connection,
    chat_id: int,
    *,
    last_answered_message_id: int,
    max_message_id: int,
    lookback_minutes: int = 45,
    max_rows: int = 12,
) -> list[dict[str, Any]]:
    """Return recent unanswered observations between last bot reply and current batch."""
    cutoff = (datetime.now(UTC) - timedelta(minutes=lookback_minutes)).isoformat()
    rows = database.execute(
        """
        select o.message_id, o.sender_id, o.sender_name, o.text, o.photo_file_id,
               o.reply_to_message_id, o.created_at,
               t.id as task_id, t.reply_to_is_bot, t.is_mention, t.decision_flag, t.decision_reason
        from observations o
        left join tasks t on t.chat_id=o.chat_id and t.message_id=o.message_id
        where o.chat_id = ?
          and o.message_id > ?
          and o.message_id <= ?
          and o.created_at >= ?
        order by o.message_id desc
        limit ?
        """,
        (chat_id, last_answered_message_id, max_message_id, cutoff, max_rows),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in reversed(rows):
        result.append(
            {
                "task_id": row["task_id"],
                "message_id": row["message_id"],
                "sender_id": row["sender_id"],
                "sender_name": row["sender_name"],
                "text": row["text"],
                "photo_file_id": row["photo_file_id"],
                "reply_to_message_id": row["reply_to_message_id"],
                "reply_to_is_bot": bool(row["reply_to_is_bot"]),
                "is_mention": bool(row["is_mention"]),
                "decision_flag": row["decision_flag"],
                "decision_reason": row["decision_reason"],
                "created_at": row["created_at"],
            }
        )
    return result


def claim(database: sqlite3.Connection, *, max_batch: int = 10) -> dict[str, Any] | None:
    """Claim oldest queued task along with any queued burst siblings in the same chat."""
    stale = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    database.execute("begin immediate")
    database.execute(
        """
        update tasks set status='queued', claimed_at=null
        where status='processing' and claimed_at < ? and attempts < 3
        """,
        (stale,),
    )
    row = database.execute(
        "select * from tasks where status='queued' and attempts < 3 order by id limit 1"
    ).fetchone()
    if row is None:
        database.commit()
        return None

    chat_id = int(row["chat_id"])
    other_rows = database.execute(
        """
        select * from tasks
        where status='queued' and attempts < 3 and chat_id=? and id != ?
        order by id limit ?
        """,
        (chat_id, row["id"], max(0, max_batch - 1)),
    ).fetchall()

    claimed_rows = [row, *other_rows]
    # Primary task becomes the latest message in the burst so replies anchor cleanly.
    primary = claimed_rows[-1]
    bundled_rows = claimed_rows[:-1]
    all_claimed_ids = [int(r["id"]) for r in claimed_rows]
    bundled_ids = [int(r["id"]) for r in bundled_rows]

    now_ts = now_iso()
    for task_id in all_claimed_ids:
        database.execute(
            "update tasks set status='processing', attempts=attempts+1, claimed_at=? where id=?",
            (now_ts, task_id),
        )

    state_row = database.execute(
        "select last_answered_message_id, last_bot_reply_at from chat_state where chat_id = ?",
        (chat_id,),
    ).fetchone()
    last_answered_message_id = int(state_row["last_answered_message_id"]) if state_row else 0
    last_bot_reply_at = state_row["last_bot_reply_at"] if state_row else None

    payload = dict(primary)
    all_settings = dict(database.execute("select key, value from settings").fetchall())
    payload["configured_group_id"] = (
        int(all_settings["chat_id"]) if all_settings.get("chat_id") else None
    )
    payload["bot_settings"] = all_settings
    source = "личный диалог" if chat_id > 0 else "группа"
    # Exclude primary itself from history before primary so it doesn't duplicate;
    # full history up to primary - 1 gives complete prior conversation.
    payload["context"] = _context_rows(
        database,
        chat_id,
        up_to_message_id=int(primary["message_id"]) - 1,
        source=source,
    )
    payload["claimed_ids"] = all_claimed_ids
    payload["bundled_ids"] = bundled_ids
    payload["bundled_tasks"] = [
        {
            "id": int(r["id"]),
            "message_id": int(r["message_id"]),
            "sender_id": int(r["sender_id"]),
            "sender_name": r["sender_name"],
            "text": r["text"],
            "photo_file_id": r["photo_file_id"],
            "reply_to_message_id": r["reply_to_message_id"],
            "reply_to_is_bot": bool(r["reply_to_is_bot"]),
            "is_mention": bool(r["is_mention"]),
            "created_at": r["created_at"],
        }
        for r in bundled_rows
    ]
    payload["unanswered_messages"] = _unanswered_rows(
        database,
        chat_id,
        last_answered_message_id=last_answered_message_id,
        max_message_id=int(primary["message_id"]),
    )
    payload["last_answered_message_id"] = last_answered_message_id
    payload["last_bot_reply_at"] = last_bot_reply_at
    database.commit()
    return payload


def all_contexts(database: sqlite3.Connection) -> list[dict[str, Any]]:
    """Export complete unlimited per-chat history for Google Drive archival."""
    rows = database.execute(
        """
        select o.chat_id, o.message_id, o.sender_id, o.sender_name, o.text,
               o.photo_file_id, o.reply_to_message_id, o.created_at,
               t.response, t.decision_flag, t.decision_reason, t.completed_at
        from observations o
        left join tasks t on t.chat_id=o.chat_id and t.message_id=o.message_id
        order by o.chat_id, o.message_id
        """
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        chat_id = int(row["chat_id"])
        entry: dict[str, Any] = {
            "message_id": row["message_id"],
            "sender_id": row["sender_id"],
            "author": row["sender_name"],
            "text": row["text"],
            "has_media": bool(row["photo_file_id"]),
            "created_at": row["created_at"],
        }
        if row["reply_to_message_id"]:
            entry["reply_to_message_id"] = row["reply_to_message_id"]
        if row["decision_flag"]:
            entry["decision_flag"] = row["decision_flag"]
        if row["decision_reason"]:
            entry["decision_reason"] = row["decision_reason"]
        grouped.setdefault(chat_id, []).append(entry)

        if row["response"] and row["response"] not in {"[[skip]]", "[[muted]]"}:
            grouped[chat_id].append(
                {
                    "message_id": row["message_id"],
                    "sender_id": 9231461559,
                    "author": "Тау бао (ГС)",
                    "text": row["response"],
                    "has_media": True,
                    "created_at": row["completed_at"] or row["created_at"],
                }
            )
    return [
        {"chat_id": chat_id, "rows": context_rows}
        for chat_id, context_rows in grouped.items()
    ]


def _parse_id_list(single_id: int | None, csv_ids: str | None) -> list[int]:
    if csv_ids:
        return [int(item.strip()) for item in csv_ids.split(",") if item.strip()]
    if single_id is not None:
        return [single_id]
    return []


def complete_tasks(
    database: sqlite3.Connection,
    *,
    task_ids: list[int],
    response: str,
    decision_flag: str | None = None,
    decision_reason: str | None = None,
    answered_up_to_message_id: int | None = None,
    primary_task_id: int | None = None,
) -> None:
    """Mark claimed tasks complete and advance answered watermark when bot replied."""
    if not task_ids:
        return
    now_ts = now_iso()
    is_actual_reply = response not in {"[[skip]]", "[[muted]]", ""}
    effective_flag = decision_flag or ("reply" if is_actual_reply else "skip")

    for tid in task_ids:
        covered_by = primary_task_id if (is_actual_reply and primary_task_id and tid != primary_task_id) else None
        task_resp = response if (not primary_task_id or tid == primary_task_id or not is_actual_reply) else "[[covered]]"
        database.execute(
            """
            update tasks
            set status='completed',
                completed_at=?,
                response=?,
                decision_flag=?,
                decision_reason=coalesce(?, decision_reason),
                covered_by_task_id=coalesce(?, covered_by_task_id),
                error=null
            where id=?
            """,
            (now_ts, task_resp, effective_flag, decision_reason, covered_by, tid),
        )

    if is_actual_reply:
        row = database.execute(
            "select chat_id, max(message_id) as max_msg from tasks where id in (%s)"
            % ",".join("?" for _ in task_ids),
            task_ids,
        ).fetchone()
        if row and row["chat_id"] is not None:
            chat_id = int(row["chat_id"])
            watermark = answered_up_to_message_id or int(row["max_msg"] or 0)
            database.execute(
                """
                insert into chat_state(chat_id, last_answered_message_id, last_bot_reply_at)
                values (?, ?, ?)
                on conflict(chat_id) do update set
                    last_answered_message_id=max(chat_state.last_answered_message_id, excluded.last_answered_message_id),
                    last_bot_reply_at=excluded.last_bot_reply_at
                """,
                (chat_id, watermark, now_ts),
            )
            if primary_task_id:
                database.execute(
                    """
                    update tasks
                    set covered_by_task_id = coalesce(covered_by_task_id, ?)
                    where chat_id = ?
                      and message_id <= ?
                      and decision_flag = 'skip'
                      and covered_by_task_id is null
                    """,
                    (primary_task_id, chat_id, watermark),
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("claim")
    commands.add_parser("contexts")
    commands.add_parser("state")

    complete = commands.add_parser("complete")
    complete.add_argument("--id", type=int, required=False)
    complete.add_argument("--ids", type=str, required=False)
    complete.add_argument("--response-base64", required=True)
    complete.add_argument("--flag", type=str, required=False)
    complete.add_argument("--reason-base64", type=str, required=False)
    complete.add_argument("--answered-up-to", type=int, required=False)
    complete.add_argument("--primary-id", type=int, required=False)

    retry = commands.add_parser("retry")
    retry.add_argument("--id", type=int, required=False)
    retry.add_argument("--ids", type=str, required=False)
    retry.add_argument("--error-base64", required=True)

    update_obs = commands.add_parser("update-observation")
    update_obs.add_argument("--chat-id", type=int, required=True)
    update_obs.add_argument("--message-id", type=int, required=True)
    update_obs.add_argument("--text-base64", required=True)

    progress = commands.add_parser("set-progress")
    progress.add_argument("--id", type=int, required=True)
    progress.add_argument("--message-id", type=int, required=True)

    last_msg = commands.add_parser("last-bot-message")
    last_msg.add_argument("--chat-id", type=int, required=True)

    track_msg = commands.add_parser("track-bot-message")
    track_msg.add_argument("--chat-id", type=int, required=True)
    track_msg.add_argument("--message-id", type=int, required=True)
    track_msg.add_argument("--task-id", type=int, required=False)

    get_msgs = commands.add_parser("get-bot-messages")
    get_msgs.add_argument("--chat-id", type=int, required=True)
    get_msgs.add_argument("--limit", type=int, default=100)

    clear_msgs = commands.add_parser("clear-bot-messages")
    clear_msgs.add_argument("--chat-id", type=int, required=True)
    clear_msgs.add_argument("--message-ids", type=str, required=False)

    args = parser.parse_args()

    with connect(args.database) as database:
        if args.command == "init":
            print(json.dumps({"status": "ready", "database": str(args.database)}))
        elif args.command == "claim":
            print(json.dumps(claim(database), ensure_ascii=False))
        elif args.command == "contexts":
            print(json.dumps(all_contexts(database), ensure_ascii=False))
        elif args.command == "state":
            values = dict(database.execute("select key, value from settings").fetchall())
            print(
                json.dumps(
                    {
                        "chat_id": int(values["chat_id"]) if values.get("chat_id") else None,
                        "muted": values.get("muted") == "1",
                    }
                )
            )
        elif args.command == "complete":
            response = base64.b64decode(args.response_base64).decode()
            reason = (
                base64.b64decode(args.reason_base64).decode()
                if args.reason_base64
                else None
            )
            target_ids = _parse_id_list(args.id, args.ids)
            complete_tasks(
                database,
                task_ids=target_ids,
                response=response,
                decision_flag=args.flag,
                decision_reason=reason,
                answered_up_to_message_id=args.answered_up_to,
                primary_task_id=args.primary_id,
            )
            print(json.dumps({"status": "completed", "ids": target_ids}))
        elif args.command == "retry":
            error = base64.b64decode(args.error_base64).decode()
            target_ids = _parse_id_list(args.id, args.ids)
            for tid in target_ids:
                database.execute(
                    """
                    update tasks set status=case when attempts >= 3 then 'failed' else 'queued' end,
                        claimed_at=null, error=? where id=?
                    """,
                    (error, tid),
                )
            print(json.dumps({"status": "retried", "ids": target_ids}))
        elif args.command == "update-observation":
            text = base64.b64decode(args.text_base64).decode()
            database.execute(
                "update observations set text = ? where chat_id = ? and message_id = ?",
                (text, args.chat_id, args.message_id),
            )
            database.execute(
                "update tasks set text = ? where chat_id = ? and message_id = ?",
                (text, args.chat_id, args.message_id),
            )
            print(json.dumps({"status": "updated", "message_id": args.message_id}))
        elif args.command == "set-progress":
            database.execute(
                "update tasks set progress_message_id=? where id=?",
                (args.message_id, args.id),
            )
            database.execute(
                """
                insert or ignore into bot_messages(chat_id, message_id, task_id, created_at)
                select chat_id, ?, id, ? from tasks where id=?
                """,
                (args.message_id, now_iso(), args.id),
            )
        elif args.command == "last-bot-message":
            row = database.execute(
                "select message_id from bot_messages where chat_id = ? order by id desc limit 1",
                (args.chat_id,),
            ).fetchone()
            print(str(row["message_id"]) if row and row["message_id"] else "")
        elif args.command == "track-bot-message":
            database.execute(
                """
                insert or ignore into bot_messages(chat_id, message_id, task_id, created_at)
                values (?, ?, ?, ?)
                """,
                (args.chat_id, args.message_id, args.task_id, now_iso()),
            )
            print(json.dumps({"status": "ok", "message_id": args.message_id}))
        elif args.command == "get-bot-messages":
            rows = database.execute(
                "select message_id from bot_messages where chat_id = ? order by id desc limit ?",
                (args.chat_id, args.limit),
            ).fetchall()
            ids = [int(r["message_id"]) for r in rows]
            print(json.dumps({"chat_id": args.chat_id, "message_ids": ids}))
        elif args.command == "clear-bot-messages":
            if args.message_ids:
                ids = [int(i.strip()) for i in args.message_ids.split(",") if i.strip()]
                placeholders = ",".join("?" for _ in ids)
                database.execute(
                    f"delete from bot_messages where chat_id=? and message_id in ({placeholders})",
                    [args.chat_id, *ids],
                )
            else:
                database.execute(
                    "delete from bot_messages where chat_id=?",
                    (args.chat_id,),
                )
            print(json.dumps({"status": "cleared", "chat_id": args.chat_id}))


if __name__ == "__main__":
    main()
