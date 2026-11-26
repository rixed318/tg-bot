from __future__ import annotations

from pathlib import Path

from tg_bot import drive_sync


def test_write_local_full_context_preserves_unlimited_rows(tmp_path: Path) -> None:
    ctx = [
        {"message_id": i, "source": "группа", "author": "User", "text": f"msg {i}", "created_at": "2026-11-26T00:00:00Z"}
        for i in range(1, 105)
    ]
    out = drive_sync.write_local_full_context({"context": ctx}, tmp_path, ())
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 104


def test_sync_contexts_payload_skips_unchanged_and_uploads_changed(tmp_path: Path, monkeypatch) -> None:
    state_file = tmp_path / "drive_state.json"
    monkeypatch.setattr(drive_sync, "DRIVE_SYNC_STATE_FILE", state_file)
    monkeypatch.setattr(drive_sync, "WORK_DIR", tmp_path)

    uploads: list[tuple[str, str]] = []

    def fake_rclone(args: list[str], *, timeout: int = 300):
        uploads.append((args[0], args[2]))
        return None

    monkeypatch.setattr(drive_sync, "_run_rclone", fake_rclone)

    payload = [{"chat_id": -1003800116774, "rows": [{"message_id": 1, "text": "hi"}]}]
    first = drive_sync.sync_contexts_payload(payload)
    assert "group-1003800116774" in first
    assert len(uploads) == 1

    # Same content -> skipped
    second = drive_sync.sync_contexts_payload(payload)
    assert second == {}
    assert len(uploads) == 1

    # Updated content -> uploaded
    payload[0]["rows"].append({"message_id": 2, "text": "hello"})
    third = drive_sync.sync_contexts_payload(payload)
    assert "group-1003800116774" in third
    assert len(uploads) == 2
