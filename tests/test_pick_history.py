"""Тесты истории PDF-подборов."""

from __future__ import annotations

from pathlib import Path

from max_ekrom_bot.pick_history import PickHistoryStore


def test_add_and_list(tmp_path: Path):
    db = tmp_path / "bot.db"
    root = tmp_path / "history"
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")

    store = PickHistoryStore(db, root, max_per_user=5, ttl_days=30)
    rec = store.add(42, "духовка чёрная", pdf, total_price=1000.0, matched_count=1)
    assert rec is not None
    assert rec.pdf_path.is_file()

    items = store.list_for_user(42)
    assert len(items) == 1
    assert items[0].query_text == "духовка чёрная"


def test_get_only_own_records(tmp_path: Path):
    db = tmp_path / "bot.db"
    root = tmp_path / "history"
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"pdf")

    store = PickHistoryStore(db, root)
    rec = store.add(1, "q", pdf, total_price=None, matched_count=1)
    assert store.get(rec.id, 1) is not None
    assert store.get(rec.id, 2) is None
