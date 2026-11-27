from __future__ import annotations

from pathlib import Path

from tg_bot.progress import (
    MediaIntent,
    detect_media_intent,
    render_progress_text,
    scan_outbox_media,
)


def test_detect_media_intent() -> None:
    i1 = detect_media_intent("Секси ксюшу сгенерируй, фото + видео")
    assert i1 == MediaIntent(wants_photo=True, wants_video=True, wants_pack=False)
    i2 = detect_media_intent("Создай эмоджи пак из этих изображений")
    assert i2 == MediaIntent(wants_photo=False, wants_video=False, wants_pack=True)
    i3 = detect_media_intent("Как дела в блендере?")
    assert i3.enabled is False


def test_render_progress_text_reflects_real_outbox_files(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    intent = MediaIntent(wants_photo=True, wants_video=True, wants_pack=False)

    t0 = render_progress_text(intent, outbox_dir=outbox, elapsed_seconds=12, stage="generate")
    assert "0/3" in t0
    assert "генерация…" in t0
    assert "ожидает фото" in t0

    (outbox / "generated.jpg").write_bytes(b"x" * 2048)
    photos, videos = scan_outbox_media(outbox)
    assert len(photos) == 1
    assert len(videos) == 0

    t1 = render_progress_text(intent, outbox_dir=outbox, elapsed_seconds=45, stage="generate")
    assert "1/3" in t1
    assert "готово (2 КБ)" in t1
    assert "🎬 Видео: <i>генерация…</i>" in t1

    (outbox / "video.mp4").write_bytes(b"v" * (2 * 1024 * 1024))
    t2 = render_progress_text(intent, outbox_dir=outbox, elapsed_seconds=90, stage="deliver")
    assert "2/3" in t2
    assert "готово (2.0 МБ)" in t2
    assert "🎙 Озвучка и отправка: <i>в процессе…</i>" in t2
