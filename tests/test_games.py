from __future__ import annotations

from pathlib import Path

from tg_bot.games import (
    format_leaderboard_html,
    get_leaderboard,
    mafia_join,
    mafia_start,
    mafia_vote,
    play_dice_duel_round,
    record_game_score,
    start_snake_game,
    step_snake_game,
)


def test_game_leaderboard_and_duel(tmp_path: Path) -> None:
    db = tmp_path / "games.db"
    record_game_score(user_id=101, username="ruslan", display_name="Ruslan", game="snake", points=42, db_path=db)
    record_game_score(user_id=202, username="", display_name="Anya", game="snake", points=88, db_path=db)
    top = get_leaderboard("snake", db_path=db)
    assert len(top) == 2
    assert top[0]["user_id"] == 202
    assert top[0]["best_score"] == 88

    msg = play_dice_duel_round(user_id=101, username="ruslan", display_name="Ruslan", db_path=db)
    assert "@ruslan" in msg
    assert "<code>101</code>" in msg
    board = format_leaderboard_html("dice_duel", db_path=db)
    assert "@ruslan" in board


def test_snake_session_steps(tmp_path: Path) -> None:
    db = tmp_path / "games.db"
    html, alive = start_snake_game(chat_id=1, user_id=101, username="ruslan", display_name="Ruslan", db_path=db)
    assert alive is True
    assert "Змейка" in html
    html2, alive2 = step_snake_game(
        chat_id=1, user_id=101, username="ruslan", display_name="Ruslan", direction="right", db_path=db
    )
    assert "Змейка" in html2
    assert isinstance(alive2, bool)


def test_mafia_lobby_start_and_vote(tmp_path: Path) -> None:
    db = tmp_path / "games.db"
    for uid in (1, 2, 3, 4):
        mafia_join(10, uid, f"u{uid}", f"User {uid}", db_path=db)
    ok, group_html, dms = mafia_start(10, db_path=db)
    assert ok is True
    assert len(dms) == 4
    assert "Дневное голосование" in group_html
    res, _ = mafia_vote(10, 1, 2, db_path=db)
    assert "Проголосовало" in res or "Победа" in res
