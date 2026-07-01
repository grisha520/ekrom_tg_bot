"""Тесты метрик для админа."""

from __future__ import annotations

from pathlib import Path

from max_ekrom_bot.bot_metrics import BotMetricsStore, format_admin_stats


def test_record_and_stats(tmp_path: Path):
    db = tmp_path / "metrics.db"
    store = BotMetricsStore(db)
    store.record(
        user_id=1,
        is_client=True,
        success=True,
        matched_count=2,
        duration_sec=12.5,
        queue_wait_sec=3.0,
    )
    stats = store.stats()
    assert stats.today_client >= 1
    text = format_admin_stats(stats)
    assert "Статистика бота" in text
    assert "12.5" in text or "среднее" in text
