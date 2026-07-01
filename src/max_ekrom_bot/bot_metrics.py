"""Метрики запросов для админа (очередь, время подбора, нагрузка)."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import timezone, timedelta
from pathlib import Path

_MSK = timezone(timedelta(hours=3))  # Москва (без перехода на летнее время)
_METRICS_TTL_SEC = 90 * 86400  # 90 суток в БД


@dataclass(frozen=True)
class AdminStats:
    today_client: int
    today_operator: int
    hour_total: int
    unique_clients_today: int
    avg_duration_sec: float
    max_queue_wait_sec: float
    p95_duration_sec: float
    success_with_items: int
    success_total: int
    failed: int


class BotMetricsStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS request_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    user_id INTEGER NOT NULL,
                    is_client INTEGER NOT NULL,
                    success INTEGER NOT NULL,
                    matched_count INTEGER NOT NULL DEFAULT 0,
                    duration_sec REAL NOT NULL,
                    queue_wait_sec REAL NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_metrics_created "
                "ON request_metrics(created_at)"
            )

    def record(
        self,
        *,
        user_id: int,
        is_client: bool,
        success: bool,
        matched_count: int,
        duration_sec: float,
        queue_wait_sec: float,
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO request_metrics "
                "(created_at, user_id, is_client, success, matched_count, "
                "duration_sec, queue_wait_sec) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    now,
                    user_id,
                    1 if is_client else 0,
                    1 if success else 0,
                    matched_count,
                    round(duration_sec, 2),
                    round(queue_wait_sec, 2),
                ),
            )
            conn.execute(
                "DELETE FROM request_metrics WHERE created_at < ?",
                (now - _METRICS_TTL_SEC,),
            )
            conn.commit()

    def stats(self) -> AdminStats:
        now = time.time()
        day_start = _start_of_today_msk(now)
        hour_ago = now - 3600

        with self._connect() as conn:
            today_client = conn.execute(
                "SELECT COUNT(*) FROM request_metrics "
                "WHERE created_at >= ? AND is_client = 1",
                (day_start,),
            ).fetchone()[0]
            today_operator = conn.execute(
                "SELECT COUNT(*) FROM request_metrics "
                "WHERE created_at >= ? AND is_client = 0",
                (day_start,),
            ).fetchone()[0]
            hour_total = conn.execute(
                "SELECT COUNT(*) FROM request_metrics WHERE created_at >= ?",
                (hour_ago,),
            ).fetchone()[0]
            unique_clients = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM request_metrics "
                "WHERE created_at >= ? AND is_client = 1",
                (day_start,),
            ).fetchone()[0]
            row = conn.execute(
                "SELECT AVG(duration_sec), MAX(queue_wait_sec) "
                "FROM request_metrics WHERE created_at >= ? AND success = 1",
                (day_start,),
            ).fetchone()
            avg_dur = float(row[0] or 0.0)
            max_queue = float(row[1] or 0.0)
            durations = [
                r[0]
                for r in conn.execute(
                    "SELECT duration_sec FROM request_metrics "
                    "WHERE created_at >= ? AND success = 1 ORDER BY duration_sec",
                    (day_start,),
                ).fetchall()
            ]
            success_with = conn.execute(
                "SELECT COUNT(*) FROM request_metrics "
                "WHERE created_at >= ? AND success = 1 AND matched_count > 0",
                (day_start,),
            ).fetchone()[0]
            success_total = conn.execute(
                "SELECT COUNT(*) FROM request_metrics "
                "WHERE created_at >= ? AND success = 1",
                (day_start,),
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM request_metrics "
                "WHERE created_at >= ? AND success = 0",
                (day_start,),
            ).fetchone()[0]

        p95 = _percentile(durations, 0.95) if durations else 0.0
        return AdminStats(
            today_client=today_client,
            today_operator=today_operator,
            hour_total=hour_total,
            unique_clients_today=unique_clients,
            avg_duration_sec=avg_dur,
            max_queue_wait_sec=max_queue,
            p95_duration_sec=p95,
            success_with_items=success_with,
            success_total=success_total,
            failed=failed,
        )


def format_admin_stats(stats: AdminStats) -> str:
    total_today = stats.today_client + stats.today_operator
    rate = (
        f"{100 * stats.success_with_items // max(stats.success_total, 1)}%"
        if stats.success_total
        else "—"
    )
    return (
        "📊 <b>Статистика бота</b> <i>(сегодня, МСК)</i>\n\n"
        f"📥 Запросов: <b>{total_today}</b> "
        f"(клиенты {stats.today_client}, операторы {stats.today_operator})\n"
        f"👤 Уникальных клиентов: <b>{stats.unique_clients_today}</b>\n"
        f"⏱ За последний час: <b>{stats.hour_total}</b> запросов\n\n"
        "<b>Время подбора</b> (успешные)\n"
        f"• среднее: <b>{stats.avg_duration_sec:.1f} с</b>\n"
        f"• 95% быстрее: <b>{stats.p95_duration_sec:.1f} с</b>\n"
        f"• макс. ожидание в очереди: <b>{stats.max_queue_wait_sec:.1f} с</b>\n\n"
        f"✅ С товарами: <b>{stats.success_with_items}</b> из {stats.success_total} "
        f"({rate})\n"
        f"❌ Ошибок/сбоев: <b>{stats.failed}</b>"
    )


def _start_of_today_msk(now: float) -> float:
    from datetime import datetime

    dt = datetime.fromtimestamp(now, tz=_MSK)
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(int(len(sorted_vals) * p), len(sorted_vals) - 1)
    return float(sorted_vals[idx])
