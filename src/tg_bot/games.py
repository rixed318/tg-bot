"""In-chat games (quick duels, Mafia lobby) and persistent Telegram user leaderboard."""

from __future__ import annotations

import os
from pathlib import Path
import random
import sqlite3
from typing import Any

DEFAULT_GAMES_DB = Path(
    os.environ.get("TG_BOT_GAMES_DB", str(Path.home() / ".cache" / "rixed-joke-bot" / "games.db"))
)


def _connect(db_path: Path = DEFAULT_GAMES_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS game_scores (
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            game TEXT NOT NULL,
            best_score INTEGER NOT NULL DEFAULT 0,
            total_points INTEGER NOT NULL DEFAULT 0,
            games_played INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, game)
        )
        """
    )
    conn.commit()
    return conn


def record_game_score(
    *,
    user_id: int,
    username: str,
    display_name: str,
    game: str,
    points: int,
    db_path: Path = DEFAULT_GAMES_DB,
) -> dict[str, Any]:
    """Record a player's round points and update their best/total score."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT best_score, total_points, games_played FROM game_scores WHERE user_id=? AND game=?",
            (user_id, game),
        ).fetchone()
        best = max(points, int(row["best_score"])) if row else points
        total = (int(row["total_points"]) if row else 0) + points
        played = (int(row["games_played"]) if row else 0) + 1
        conn.execute(
            """
            INSERT INTO game_scores (user_id, username, display_name, game, best_score, total_points, games_played, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, game) DO UPDATE SET
                username=excluded.username,
                display_name=excluded.display_name,
                best_score=excluded.best_score,
                total_points=excluded.total_points,
                games_played=excluded.games_played,
                updated_at=CURRENT_TIMESTAMP
            """,
            (user_id, username or "", display_name or str(user_id), game, best, total, played),
        )
        conn.commit()
    return {"best_score": best, "total_points": total, "games_played": played}


def get_leaderboard(
    game: str = "dice_duel",
    *,
    limit: int = 10,
    db_path: Path = DEFAULT_GAMES_DB,
) -> list[dict[str, Any]]:
    """Return top players ordered by total_points then best_score."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT user_id, username, display_name, best_score, total_points, games_played
            FROM game_scores
            WHERE game=?
            ORDER BY total_points DESC, best_score DESC
            LIMIT ?
            """,
            (game, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def play_dice_duel_round(
    *,
    user_id: int,
    username: str,
    display_name: str,
    db_path: Path = DEFAULT_GAMES_DB,
) -> str:
    """Roll a quick chat duel round, save score by Telegram user_id, and format result."""
    player_roll = random.randint(1, 100)
    bot_roll = random.randint(1, 100)
    if player_roll > bot_roll:
        points = player_roll
        verdict = "🏆 Победа!"
    elif player_roll == bot_roll:
        points = 50
        verdict = "🤝 Ничья!"
    else:
        points = max(5, player_roll // 4)
        verdict = "💀 Бот перекинул!"
    stats = record_game_score(
        user_id=user_id,
        username=username,
        display_name=display_name,
        game="dice_duel",
        points=points,
        db_path=db_path,
    )
    who = f"@{username}" if username else display_name
    return (
        f"🎲 <b>Дуэль: {who}</b> (ID: <code>{user_id}</code>)\n"
        f"Твой бросок: <b>{player_roll}</b> | Бот: <b>{bot_roll}</b> — {verdict}\n"
        f"Начислено: <b>+{points}</b> | Всего: <b>{stats['total_points']}</b> | Рекорд: <b>{stats['best_score']}</b>"
    )


def format_leaderboard_html(game: str = "dice_duel", *, db_path: Path = DEFAULT_GAMES_DB) -> str:
    rows = get_leaderboard(game, db_path=db_path)
    if not rows:
        return "🏆 <b>Таблица рекордов пуста</b>\nСыграй первый раунд!"
    lines = ["🏆 <b>Таблица рекордов чата:</b>\n"]
    for idx, row in enumerate(rows, start=1):
        handle = f"@{row['username']}" if row["username"] else row["display_name"]
        lines.append(
            f"{idx}. <b>{handle}</b> (<code>{row['user_id']}</code>) — "
            f"<b>{row['total_points']}</b> очк. (макс: {row['best_score']}, игр: {row['games_played']})"
        )
    return "\n".join(lines)
