from __future__ import annotations

import json
from pathlib import Path

from tg_bot import worker
from tg_bot.trigger import TriggerDecision
from tg_bot.voice_tts import VoiceResult


def test_worker_skip_then_consolidated_voice_reply(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(worker, "WORK_DIR", tmp_path)
    monkeypatch.setattr(worker, "GROUP_DEBOUNCE_SECONDS", 0.0)
    monkeypatch.setattr(worker, "_schedule_drive_sync", lambda force=False: None)
    monkeypatch.setattr(worker, "archive_on_drive", lambda *a, **kw: "gdrive:ok")
    monkeypatch.setattr(worker, "send_chat_action", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "group_is_muted", lambda: False)

    calls: list[tuple[str, tuple[str, ...]]] = []

    task_skip = {
        "id": 10,
        "chat_id": -1003800116774,
        "thread_id": None,
        "message_id": 101,
        "sender_id": 1,
        "sender_name": "Никита",
        "text": "Блендер опять завис",
        "reply_to_is_bot": False,
        "is_mention": False,
        "claimed_ids": [10],
        "context": [],
        "unanswered_messages": [
            {"task_id": 10, "message_id": 101, "sender_name": "Никита", "text": "Блендер опять завис"}
        ],
    }

    task_reply = {
        "id": 12,
        "chat_id": -1003800116774,
        "thread_id": None,
        "message_id": 103,
        "sender_id": 2,
        "sender_name": "Серёжа",
        "text": "На каком движке рендеришь?",
        "reply_to_is_bot": False,
        "is_mention": False,
        "claimed_ids": [12],
        "context": [
            {"message_id": 101, "author": "Никита", "text": "Блендер опять завис", "decision_flag": "skip"},
            {"message_id": 102, "author": "Влад", "text": "У меня тоже вылетает", "decision_flag": "skip"},
        ],
        "unanswered_messages": [
            {"task_id": 10, "message_id": 101, "sender_name": "Никита", "text": "Блендер опять завис"},
            {"task_id": 11, "message_id": 102, "sender_name": "Влад", "text": "У меня тоже вылетает"},
            {"task_id": 12, "message_id": 103, "sender_name": "Серёжа", "text": "На каком движке рендеришь?"},
        ],
    }

    queue_items = [task_skip, task_reply]
    monkeypatch.setattr(worker, "claim_task", lambda: queue_items.pop(0) if queue_items else None)

    def fake_remote_queue(*args: str) -> str:
        calls.append(("remote_queue", args))
        return "{}"

    monkeypatch.setattr(worker, "remote_queue", fake_remote_queue)

    decisions = [
        TriggerDecision(should_reply=False, flag="skip", reason="рано влезать", target_message_ids=()),
        TriggerDecision(
            should_reply=True,
            flag="reply",
            reason="накопилось 3 сообщения про блендер",
            target_message_ids=(101, 102, 103),
        ),
    ]
    monkeypatch.setattr(worker, "evaluate_trigger", lambda *a, **kw: decisions.pop(0))

    voices_sent: list[dict[str, object]] = []

    def fake_synth(raw_text: str, out_ogg: Path, **kw) -> VoiceResult:
        out_ogg.write_bytes(b"OggSfake")
        return VoiceResult(
            ogg_path=out_ogg,
            duration_seconds=4,
            backend="f5_tts_ru",
            voice_name="custom",
            spoken_text=raw_text,
        )

    def fake_send_voice(token: str, task: dict, ogg: Path, *, duration_seconds=None, reply_to_message_id=None) -> int:
        voices_sent.append(
            {
                "chat_id": task["chat_id"],
                "reply_to": reply_to_message_id,
                "dur": duration_seconds,
                "bytes": ogg.read_bytes(),
            }
        )
        return 777

    monkeypatch.setattr(worker, "synthesize_telegram_voice", fake_synth)
    monkeypatch.setattr(worker, "send_voice", fake_send_voice)
    monkeypatch.setattr(
        worker,
        "_ask_gemini_reply",
        lambda *a, **kw: "Парни, проверьте оперативку и перейдите на Сайклз с Оптиксом.",
    )

    # 1. Process skipped message -> NO voice sent, completed with [[skip]]
    assert worker.process_one("tok") is True
    assert len(voices_sent) == 0
    assert calls[-1][1][0] == "complete"
    assert "--flag" in calls[-1][1] and "skip" in calls[-1][1]
    assert "--answered-up-to" not in calls[-1][1]

    # 2. Process 3rd message -> ONE consolidated voice sent, watermark updated to 103
    assert worker.process_one("tok") is True
    assert len(voices_sent) == 1
    assert voices_sent[0]["reply_to"] == 103
    last_complete = calls[-1][1]
    assert last_complete[0] == "complete"
    assert "--answered-up-to" in last_complete
    idx = last_complete.index("--answered-up-to")
    assert last_complete[idx + 1] == "103"
