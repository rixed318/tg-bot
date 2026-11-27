from __future__ import annotations

from pathlib import Path

from tg_bot.games import format_leaderboard_html, get_leaderboard, play_dice_duel_round, record_game_score


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
