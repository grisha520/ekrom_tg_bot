"""История клиентских PDF-подборов (SQLite + файлы на диске)."""

from __future__ import annotations

import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PickRecord:
    id: int
    user_id: int
    query_text: str
    pdf_path: Path
    total_price: float | None
    matched_count: int
    created_at: float


class PickHistoryStore:
    """Хранит метаданные в SQLite, PDF — в data/history/{user_id}/."""

    def __init__(
        self,
        db_path: Path,
        files_root: Path,
        *,
        max_per_user: int = 20,
        ttl_days: int = 30,
    ) -> None:
        self._db_path = db_path
        self._files_root = files_root
        self._max_per_user = max_per_user
        self._ttl_sec = ttl_days * 86400
        db_path.parent.mkdir(parents=True, exist_ok=True)
        files_root.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pick_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    query_text TEXT NOT NULL,
                    pdf_path TEXT NOT NULL,
                    total_price REAL,
                    matched_count INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pick_user_created "
                "ON pick_history(user_id, created_at DESC)"
            )

    def add(
        self,
        user_id: int,
        query_text: str,
        source_pdf: Path,
        *,
        total_price: float | None,
        matched_count: int,
    ) -> PickRecord | None:
        """Скопировать PDF в историю пользователя и записать метаданные."""
        if not source_pdf.is_file():
            return None
        now = time.time()
        user_dir = self._files_root / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)

        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO pick_history "
                "(user_id, query_text, pdf_path, total_price, matched_count, created_at) "
                "VALUES (?, ?, '', ?, ?, ?)",
                (user_id, query_text.strip()[:500], total_price, matched_count, now),
            )
            record_id = int(cur.lastrowid)
            dest = user_dir / f"{record_id}.pdf"
            shutil.copy2(source_pdf, dest)
            conn.execute(
                "UPDATE pick_history SET pdf_path = ? WHERE id = ?",
                (str(dest), record_id),
            )
            self._prune_user(conn, user_id, now)
            conn.commit()

        return PickRecord(
            id=record_id,
            user_id=user_id,
            query_text=query_text.strip()[:500],
            pdf_path=dest,
            total_price=total_price,
            matched_count=matched_count,
            created_at=now,
        )

    def _prune_user(self, conn: sqlite3.Connection, user_id: int, now: float) -> None:
        cutoff = now - self._ttl_sec
        old = conn.execute(
            "SELECT id, pdf_path FROM pick_history "
            "WHERE user_id = ? AND (created_at < ? OR id NOT IN ("
            "  SELECT id FROM pick_history WHERE user_id = ? "
            "  ORDER BY created_at DESC LIMIT ?"
            "))",
            (user_id, cutoff, user_id, self._max_per_user),
        ).fetchall()
        if not old:
            return
        ids = [r["id"] for r in old]
        conn.execute(
            f"DELETE FROM pick_history WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
        for row in old:
            try:
                Path(row["pdf_path"]).unlink(missing_ok=True)
            except OSError:
                pass

    def list_for_user(self, user_id: int, *, limit: int = 10) -> list[PickRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pick_history WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get(self, record_id: int, user_id: int) -> PickRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pick_history WHERE id = ? AND user_id = ?",
                (record_id, user_id),
            ).fetchone()
        return self._row_to_record(row) if row else None

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> PickRecord:
        return PickRecord(
            id=row["id"],
            user_id=row["user_id"],
            query_text=row["query_text"],
            pdf_path=Path(row["pdf_path"]),
            total_price=row["total_price"],
            matched_count=row["matched_count"],
            created_at=row["created_at"],
        )
