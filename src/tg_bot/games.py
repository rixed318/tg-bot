"""In-chat games (Dice Duel, interactive Snake, Mafia group lobby) and SQLite leaderboard."""

from __future__ import annotations

import json
import os
from pathlib import Path
import random
import sqlite3
from typing import Any

def _default_games_db() -> Path:
    env = os.environ.get("TG_BOT_GAMES_DB")
    if env:
        return Path(env)
    vps_dir = Path("/var/lib/rixed-joke-bot")
    if vps_dir.is_dir() and os.access(vps_dir, os.W_OK):
        return vps_dir / "games.db"
    return Path.home() / ".cache" / "rixed-joke-bot" / "games.db"


DEFAULT_GAMES_DB = _default_games_db()

SNAKE_SIZE = 7
_DIRS = {
    "up": (-1, 0),
    "down": (1, 0),
    "left": (0, -1),
    "right": (0, 1),
}


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snake_sessions (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mafia_lobbies (
            chat_id INTEGER PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mines_sessions (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, user_id)
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
    titles = {"dice_duel": "🎲 Кости", "snake": "🐍 Змейка", "mafia": "🕵️ Мафия", "mines": "💣 Сапёр"}
    title = titles.get(game, game)
    rows = get_leaderboard(game, db_path=db_path)
    if not rows:
        return f"🏆 <b>Рекорды ({title}) пока пусты</b>\nСыграй первый раунд!"
    lines = [f"🏆 <b>Рекорды чата ({title}):</b>\n"]
    for idx, row in enumerate(rows, start=1):
        handle = f"@{row['username']}" if row["username"] else row["display_name"]
        lines.append(
            f"{idx}. <b>{handle}</b> (<code>{row['user_id']}</code>) — "
            f"<b>{row['total_points']}</b> очк. (макс: {row['best_score']}, игр: {row['games_played']})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Interactive Snake (7x7 inline grid)
# ---------------------------------------------------------------------------

def _spawn_apple(snake: list[list[int]]) -> list[int]:
    occupied = {tuple(p) for p in snake}
    free = [[r, c] for r in range(SNAKE_SIZE) for c in range(SNAKE_SIZE) if (r, c) not in occupied]
    return random.choice(free) if free else [0, 0]


def _render_snake_html(state: dict[str, Any], *, alive: bool, note: str = "") -> str:
    snake = [tuple(p) for p in state["snake"]]
    head = snake[0]
    body = set(snake[1:])
    apple = tuple(state["apple"])
    grid_lines: list[str] = []
    for r in range(SNAKE_SIZE):
        row_cells: list[str] = []
        for c in range(SNAKE_SIZE):
            pos = (r, c)
            if pos == head:
                row_cells.append("🟢" if alive else "💥")
            elif pos in body:
                row_cells.append("🟩")
            elif pos == apple:
                row_cells.append("🍎")
            else:
                row_cells.append("⬛")
        grid_lines.append("".join(row_cells))
    header = (
        f"🐍 <b>Змейка</b> | Очки: <b>{state['score']}</b> | Длина: <b>{len(snake)}</b>"
    )
    if note:
        header += f"\n{note}"
    return f"{header}\n<pre>{chr(10).join(grid_lines)}</pre>"


def start_snake_game(
    *,
    chat_id: int,
    user_id: int,
    username: str,
    display_name: str,
    db_path: Path = DEFAULT_GAMES_DB,
) -> tuple[str, bool]:
    mid = SNAKE_SIZE // 2
    snake = [[mid, mid], [mid, mid - 1]]
    state = {
        "snake": snake,
        "apple": _spawn_apple(snake),
        "score": 0,
        "alive": True,
        "username": username or "",
        "display_name": display_name or str(user_id),
    }
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO snake_sessions (chat_id, user_id, state_json, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                state_json=excluded.state_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (chat_id, user_id, json.dumps(state)),
        )
        conn.commit()
    return _render_snake_html(state, alive=True, note="Управляй кнопками ниже!"), True


def step_snake_game(
    *,
    chat_id: int,
    user_id: int,
    username: str,
    display_name: str,
    direction: str,
    db_path: Path = DEFAULT_GAMES_DB,
) -> tuple[str, bool]:
    if direction not in _DIRS:
        return start_snake_game(
            chat_id=chat_id, user_id=user_id, username=username, display_name=display_name, db_path=db_path
        )
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT state_json FROM snake_sessions WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
    if not row:
        return start_snake_game(
            chat_id=chat_id, user_id=user_id, username=username, display_name=display_name, db_path=db_path
        )
    state = json.loads(row["state_json"])
    if not state.get("alive", True):
        return _render_snake_html(state, alive=False, note="Игра окончена — нажми «🔄 Новая игра»."), False

    dr, dc = _DIRS[direction]
    head_r, head_c = state["snake"][0]
    nr, nc = head_r + dr, head_c + dc
    ate = [nr, nc] == state["apple"]
    body_check = state["snake"] if ate else state["snake"][:-1]

    if nr < 0 or nr >= SNAKE_SIZE or nc < 0 or nc >= SNAKE_SIZE or [nr, nc] in body_check:
        state["alive"] = False
        stats = record_game_score(
            user_id=user_id,
            username=username,
            display_name=display_name,
            game="snake",
            points=int(state["score"]),
            db_path=db_path,
        )
        with _connect(db_path) as conn:
            conn.execute(
                "UPDATE snake_sessions SET state_json=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND user_id=?",
                (json.dumps(state), chat_id, user_id),
            )
            conn.commit()
        note = (
            f"💀 <b>Столкновение!</b> Итог: <b>{state['score']}</b> очк. "
            f"(рекорд: <b>{stats['best_score']}</b>)"
        )
        return _render_snake_html(state, alive=False, note=note), False

    new_snake = [[nr, nc]] + state["snake"]
    if ate:
        state["score"] += 10
        state["snake"] = new_snake
        state["apple"] = _spawn_apple(new_snake)
        note = "🍎 Ням! +10 очков"
    else:
        state["snake"] = new_snake[:-1]
        note = ""

    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE snake_sessions SET state_json=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND user_id=?",
            (json.dumps(state), chat_id, user_id),
        )
        conn.commit()
    return _render_snake_html(state, alive=True, note=note), True


# ---------------------------------------------------------------------------
# Mafia group lobby
# ---------------------------------------------------------------------------

def _load_mafia(chat_id: int, db_path: Path) -> dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT state_json FROM mafia_lobbies WHERE chat_id=?", (chat_id,)).fetchone()
    if row:
        return json.loads(row["state_json"])
    return {"phase": "lobby", "players": {}, "votes": {}}


def _save_mafia(chat_id: int, state: dict[str, Any], db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO mafia_lobbies (chat_id, state_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                state_json=excluded.state_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (chat_id, json.dumps(state)),
        )
        conn.commit()


def _player_label(p: dict[str, Any]) -> str:
    return f"@{p['username']}" if p.get("username") else str(p.get("display_name") or p["user_id"])


def mafia_status_html(chat_id: int, *, db_path: Path = DEFAULT_GAMES_DB) -> str:
    state = _load_mafia(chat_id, db_path)
    players = list(state["players"].values())
    phase = state.get("phase", "lobby")
    if phase == "lobby":
        plist = "\n".join(f"• {_player_label(p)}" for p in players) or "<i>Пока никого — жми «Присоединиться»!</i>"
        return (
            f"🕵️ <b>Мафия — Лобби</b> ({len(players)}/12, мин. 4)\n\n"
            f"{plist}\n\n"
            "<i>Нажми «Начать игру», и бот разошлёт роли каждому в личку.</i>"
        )
    alive = [p for p in players if p.get("alive", True)]
    plist = "\n".join(f"• {_player_label(p)}" for p in alive)
    return (
        f"☀️ <b>Мафия — Дневное голосование</b>\n"
        f"В живых: <b>{len(alive)}</b>\n\n{plist}\n\n"
        "<i>Выберите подозреваемого на выбывание:</i>"
    )


def mafia_join(
    chat_id: int, user_id: int, username: str, display_name: str, *, db_path: Path = DEFAULT_GAMES_DB
) -> str:
    state = _load_mafia(chat_id, db_path)
    if state.get("phase") != "lobby":
        return "⚠️ Игра уже идёт — дождись завершения раунда."
    uid = str(user_id)
    if uid in state["players"]:
        return "Ты уже в лобби!"
    if len(state["players"]) >= 12:
        return "Лобби заполнено (максимум 12 игроков)."
    state["players"][uid] = {
        "user_id": user_id,
        "username": username or "",
        "display_name": display_name or uid,
        "alive": True,
        "role": "peace",
    }
    _save_mafia(chat_id, state, db_path)
    return "✅ Ты в игре!"


def mafia_leave(chat_id: int, user_id: int, *, db_path: Path = DEFAULT_GAMES_DB) -> str:
    state = _load_mafia(chat_id, db_path)
    if state.get("phase") != "lobby":
        return "Нельзя выйти посреди партии."
    state["players"].pop(str(user_id), None)
    _save_mafia(chat_id, state, db_path)
    return "👋 Вышел из лобби."


def mafia_reset(chat_id: int, *, db_path: Path = DEFAULT_GAMES_DB) -> None:
    _save_mafia(chat_id, {"phase": "lobby", "players": {}, "votes": {}}, db_path)


def mafia_start(chat_id: int, *, db_path: Path = DEFAULT_GAMES_DB) -> tuple[bool, str, list[tuple[int, str]]]:
    state = _load_mafia(chat_id, db_path)
    players = list(state["players"].values())
    if len(players) < 4:
        return False, "Нужно минимум 4 игрока в лобби, чтобы начать Мафию!", []

    uids = [str(p["user_id"]) for p in players]
    random.shuffle(uids)
    n_mafia = 2 if len(uids) >= 7 else 1
    roles: dict[str, str] = {}
    for i, uid in enumerate(uids):
        if i < n_mafia:
            roles[uid] = "mafia"
        elif i == n_mafia:
            roles[uid] = "doctor"
        elif i == n_mafia + 1:
            roles[uid] = "sheriff"
        else:
            roles[uid] = "peace"

    role_labels = {
        "mafia": "🔪 <b>Мафия</b> — скрывайся днём и устраняй мирных.",
        "doctor": "💉 <b>Доктор</b> — защищай город и вычисляй мафию.",
        "sheriff": "⭐ <b>Шериф</b> — ищи мафию и веди голосование.",
        "peace": "👤 <b>Мирный житель</b> — вычисли мафию на дневном голосовании.",
    }
    dms: list[tuple[int, str]] = []
    for uid, p in state["players"].items():
        p["role"] = roles[uid]
        p["alive"] = True
        dms.append((int(uid), f"🕵️ <b>Твоя роль в Мафии:</b>\n{role_labels[roles[uid]]}"))

    state["phase"] = "day"
    state["votes"] = {}
    _save_mafia(chat_id, state, db_path)
    return True, mafia_status_html(chat_id, db_path=db_path), dms


def mafia_get_alive_players(chat_id: int, *, db_path: Path = DEFAULT_GAMES_DB) -> list[dict[str, Any]]:
    state = _load_mafia(chat_id, db_path)
    return [p for p in state["players"].values() if p.get("alive", True)]


def mafia_vote(chat_id: int, voter_id: int, target_id: int, *, db_path: Path = DEFAULT_GAMES_DB) -> tuple[str, bool]:
    """Cast a daytime lynch vote; resolve when all alive players voted. Returns (text, finished)."""
    state = _load_mafia(chat_id, db_path)
    if state.get("phase") != "day":
        return mafia_status_html(chat_id, db_path=db_path), False
    v_str = str(voter_id)
    t_str = str(target_id)
    if v_str not in state["players"] or not state["players"][v_str].get("alive", True):
        return "⚠️ Голосовать могут только живые участники партии.", False
    if t_str not in state["players"] or not state["players"][t_str].get("alive", True):
        return "⚠️ Неверная цель голосования.", False

    state["votes"][v_str] = t_str
    alive = [p for p in state["players"].values() if p.get("alive", True)]
    if len(state["votes"]) < len(alive):
        _save_mafia(chat_id, state, db_path)
        return (
            f"{mafia_status_html(chat_id, db_path=db_path)}\n\n"
            f"🗳 Проголосовало: <b>{len(state['votes'])}/{len(alive)}</b>"
        ), False

    counts: dict[str, int] = {}
    for tid in state["votes"].values():
        counts[tid] = counts.get(tid, 0) + 1
    lynched_uid = max(counts, key=lambda k: counts[k])
    lynched = state["players"][lynched_uid]
    lynched["alive"] = False
    state["votes"] = {}

    alive_after = [p for p in state["players"].values() if p.get("alive", True)]
    mafia_alive = [p for p in alive_after if p.get("role") == "mafia"]
    town_alive = [p for p in alive_after if p.get("role") != "mafia"]

    if not mafia_alive or len(mafia_alive) >= len(town_alive):
        town_won = not mafia_alive
        winners = [
            p for p in state["players"].values()
            if (p.get("role") != "mafia") == town_won
        ]
        for w in winners:
            record_game_score(
                user_id=int(w["user_id"]),
                username=str(w.get("username") or ""),
                display_name=str(w.get("display_name") or w["user_id"]),
                game="mafia",
                points=100,
                db_path=db_path,
            )
        verdict = "🎉 <b>Победа Мирных!</b>" if town_won else "🔪 <b>Победа Мафии!</b>"
        summary = (
            f"⚖️ Город изгнал <b>{_player_label(lynched)}</b>.\n\n"
            f"{verdict}\n<i>Победителям начислено +100 очков в рейтинг Мафии!</i>"
        )
        mafia_reset(chat_id, db_path=db_path)
        return summary, True

    _save_mafia(chat_id, state, db_path)
    return (
        f"⚖️ Город изгнал <b>{_player_label(lynched)}</b>, но мафия ещё среди вас!\n\n"
        + mafia_status_html(chat_id, db_path=db_path)
    ), False


# ---------------------------------------------------------------------------
# Interactive Minesweeper (5x5 inline grid, 4 mines)
# ---------------------------------------------------------------------------

MINES_SIZE = 5
MINES_COUNT = 4


def _mines_adj(mines: set[tuple[int, int]], r: int, c: int) -> int:
    return sum(
        1
        for dr in (-1, 0, 1)
        for dc in (-1, 0, 1)
        if (dr or dc) and (r + dr, c + dc) in mines
    )


def _render_mines_grid(state: dict[str, Any]) -> list[list[str]]:
    mines = {tuple(m) for m in state["mines"]}
    opened = {tuple(o) for o in state["opened"]}
    alive = state.get("alive", True)
    won = state.get("won", False)
    nums = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
    grid: list[list[str]] = []
    for r in range(MINES_SIZE):
        row: list[str] = []
        for c in range(MINES_SIZE):
            if (r, c) in mines and not alive:
                row.append("💥")
            elif (r, c) in mines and won:
                row.append("🚩")
            elif (r, c) in opened:
                cnt = _mines_adj(mines, r, c)
                row.append("🟦" if cnt == 0 else nums[cnt])
            else:
                row.append("⬜")
        grid.append(row)
    return grid


def start_mines_game(
    *,
    chat_id: int,
    user_id: int,
    db_path: Path = DEFAULT_GAMES_DB,
) -> tuple[str, list[list[str]], bool]:
    cells = [[r, c] for r in range(MINES_SIZE) for c in range(MINES_SIZE)]
    mines = random.sample(cells, MINES_COUNT)
    state = {"mines": mines, "opened": [], "alive": True, "won": False}
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO mines_sessions (chat_id, user_id, state_json, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                state_json=excluded.state_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (chat_id, user_id, json.dumps(state)),
        )
        conn.commit()
    return (
        f"💣 <b>Сапёр 5×5</b> | Мин: <b>{MINES_COUNT}</b> | Открыто: <b>0/{MINES_SIZE*MINES_SIZE - MINES_COUNT}</b>",
        _render_mines_grid(state),
        True,
    )


def step_mines_game(
    *,
    chat_id: int,
    user_id: int,
    username: str,
    display_name: str,
    row: int,
    col: int,
    db_path: Path = DEFAULT_GAMES_DB,
) -> tuple[str, list[list[str]], bool]:
    with _connect(db_path) as conn:
        rec = conn.execute(
            "SELECT state_json FROM mines_sessions WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
    if not rec:
        return start_mines_game(chat_id=chat_id, user_id=user_id, db_path=db_path)
    state = json.loads(rec["state_json"])
    if not state.get("alive", True) or state.get("won", False):
        return (
            "💣 <b>Сапёр 5×5</b> — партия завершена, жми «🔄 Новая игра».",
            _render_mines_grid(state),
            False,
        )

    mines = {tuple(m) for m in state["mines"]}
    opened = {tuple(o) for o in state["opened"]}
    target = (row, col)
    if target in mines:
        state["alive"] = False
        pts = len(opened) * 2
        if pts > 0:
            record_game_score(
                user_id=user_id, username=username, display_name=display_name, game="mines", points=pts, db_path=db_path
            )
        with _connect(db_path) as conn:
            conn.execute(
                "UPDATE mines_sessions SET state_json=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND user_id=?",
                (json.dumps(state), chat_id, user_id),
            )
            conn.commit()
        return f"💥 <b>Бум!</b> Нарвался на мину. Очки за проход: <b>+{pts}</b>", _render_mines_grid(state), False

    # Flood-fill zero-adjacent cells
    stack = [target]
    while stack:
        cr, cc = stack.pop()
        if (cr, cc) in opened or (cr, cc) in mines:
            continue
        opened.add((cr, cc))
        if _mines_adj(mines, cr, cc) == 0:
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    nr, nc = cr + dr, cc + dc
                    if 0 <= nr < MINES_SIZE and 0 <= nc < MINES_SIZE and (nr, nc) not in opened:
                        stack.append((nr, nc))

    state["opened"] = [list(p) for p in opened]
    safe_total = MINES_SIZE * MINES_SIZE - MINES_COUNT
    if len(opened) >= safe_total:
        state["won"] = True
        stats = record_game_score(
            user_id=user_id, username=username, display_name=display_name, game="mines", points=60, db_path=db_path
        )
        with _connect(db_path) as conn:
            conn.execute(
                "UPDATE mines_sessions SET state_json=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND user_id=?",
                (json.dumps(state), chat_id, user_id),
            )
            conn.commit()
        return (
            f"🏆 <b>Поле зачищено! +60 очков</b> (Всего: <b>{stats['total_points']}</b>)",
            _render_mines_grid(state),
            False,
        )

    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE mines_sessions SET state_json=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND user_id=?",
            (json.dumps(state), chat_id, user_id),
        )
        conn.commit()
    return (
        f"💣 <b>Сапёр 5×5</b> | Мин: <b>{MINES_COUNT}</b> | Открыто: <b>{len(opened)}/{safe_total}</b>",
        _render_mines_grid(state),
        True,
    )
