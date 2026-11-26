from __future__ import annotations

import os
from pathlib import Path

from tg_bot.queue_db import (
    all_contexts,
    claim,
    complete_tasks,
    connect,
    enqueue,
    get_group,
    is_muted,
    remember_message,
    remember_participant,
    set_group,
    set_muted,
    update_observation_text,
)


def test_unlimited_context_has_no_40_message_cap(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    set_group(-1003800116774, "Блендер", db_path)
    assert get_group(db_path) == (-1003800116774, "Блендер")

    for i in range(1, 76):
        remember_message(
            chat_id=-1003800116774,
            message_id=i,
            sender_id=10 + (i % 3),
            sender_name=f"User{i % 3}",
            text=f"Сообщение номер {i}",
            path=db_path,
        )
    enqueue(
        chat_id=-1003800116774,
        thread_id=None,
        message_id=76,
        reply_to_message_id=None,
        sender_id=11,
        sender_name="User1",
        text="Последнее сообщение 76",
        reply_context=None,
        path=db_path,
    )
    remember_message(
        chat_id=-1003800116774,
        message_id=76,
        sender_id=11,
        sender_name="User1",
        text="Последнее сообщение 76",
        path=db_path,
    )
    with connect(db_path) as db:
        claimed = claim(db)
    assert claimed is not None
    assert len(claimed["context"]) == 75
    assert claimed["context"][0]["message_id"] == 1
    assert claimed["context"][-1]["message_id"] == 75


def test_trigger_skip_accumulates_unanswered_then_reply_clears(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    set_group(-1003800116774, "Блендер", db_path)

    # Message 1 -> skip
    remember_message(
        chat_id=-1003800116774, message_id=101, sender_id=1, sender_name="Никита", text="Парни блендер упал", path=db_path
    )
    enqueue(
        chat_id=-1003800116774, thread_id=None, message_id=101, reply_to_message_id=None,
        sender_id=1, sender_name="Никита", text="Парни блендер упал", reply_context=None, path=db_path
    )
    with connect(db_path) as db:
        t1 = claim(db)
        assert t1 is not None
        assert [m["message_id"] for m in t1["unanswered_messages"]] == [101]
        complete_tasks(
            db, task_ids=[t1["id"]], response="[[skip]]", decision_flag="skip", decision_reason="ждем продолжения"
        )

    # Message 2 -> skip, unanswered now has [101, 102]
    remember_message(
        chat_id=-1003800116774, message_id=102, sender_id=2, sender_name="Серёжа", text="На Cycles?", path=db_path
    )
    enqueue(
        chat_id=-1003800116774, thread_id=None, message_id=102, reply_to_message_id=None,
        sender_id=2, sender_name="Серёжа", text="На Cycles?", reply_context=None, path=db_path
    )
    with connect(db_path) as db:
        t2 = claim(db)
        assert t2 is not None
        assert [m["message_id"] for m in t2["unanswered_messages"]] == [101, 102]
        complete_tasks(
            db, task_ids=[t2["id"]], response="[[skip]]", decision_flag="skip", decision_reason="еще не все ясно"
        )

    # Message 3 -> reply! unanswered has [101, 102, 103]
    remember_message(
        chat_id=-1003800116774, message_id=103, sender_id=1, sender_name="Никита", text="Да, вылетает с ошибкой памяти", path=db_path
    )
    enqueue(
        chat_id=-1003800116774, thread_id=None, message_id=103, reply_to_message_id=None,
        sender_id=1, sender_name="Никита", text="Да, вылетает с ошибкой памяти", reply_context=None, path=db_path
    )
    with connect(db_path) as db:
        t3 = claim(db)
        assert t3 is not None
        assert [m["message_id"] for m in t3["unanswered_messages"]] == [101, 102, 103]
        complete_tasks(
            db,
            task_ids=[t3["id"]],
            response="Ребят, по вашему крашу в Сайклз проверьте лимит вирам и тайлинг.",
            decision_flag="reply",
            decision_reason="накопилось 3 сообщения про краш Cycles",
            answered_up_to_message_id=103,
            primary_task_id=t3["id"],
        )

    # Message 4 -> unanswered only has [104], while full context has 101..103 + bot reply
    remember_message(
        chat_id=-1003800116774, message_id=104, sender_id=1, sender_name="Никита", text="О, спасибо", path=db_path
    )
    enqueue(
        chat_id=-1003800116774, thread_id=None, message_id=104, reply_to_message_id=None,
        sender_id=1, sender_name="Никита", text="О, спасибо", reply_context=None, path=db_path
    )
    with connect(db_path) as db:
        t4 = claim(db)
        assert t4 is not None
        assert [m["message_id"] for m in t4["unanswered_messages"]] == [104]
        assert len(t4["context"]) == 4  # 101, 102, 103, and bot's reply


def test_update_observation_persists_voice_transcript(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    remember_message(
        chat_id=-1003800116774,
        message_id=55,
        sender_id=7,
        sender_name="Влад",
        text="[К сообщению прикреплено: голосовое сообщение]",
        photo_file_id="voice_123",
        path=db_path,
    )
    update_observation_text(
        chat_id=-1003800116774,
        message_id=55,
        text="[К сообщению прикреплено: голосовое сообщение]\n[Расшифровка]: «Проверь ноды в блендере»",
        path=db_path,
    )
    with connect(db_path) as db:
        contexts = all_contexts(db)
    assert len(contexts) == 1
    assert "Проверь ноды в блендере" in contexts[0]["rows"][0]["text"]


def test_sqlite_connect_closes_file_descriptors(tmp_path: Path) -> None:
    db_path = tmp_path / "fd_test.db"
    set_muted(False, db_path)
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(250):
        assert is_muted(db_path) is False
        remember_participant(1, "user", "User", path=db_path)
    after = len(os.listdir("/proc/self/fd"))
    assert after - before < 5
