"""Кэш привязки характеристик: «текст позиции + схема категории» → ограничения.

Зачем. Один запрос клиента стоит ~4 ₽, и ~88 % из них — вызов YandexGPT Pro в
схемо-маппере: модели каждый раз отправляется список атрибутов категории, чтобы
привязать «чёрный», «60 см», «газовая» к реальным полям карточки. Формулировки
клиентов повторяются, ответ для одинаковой пары «текст + схема» всегда один —
значит, его можно запомнить. Ожидаемо после первых сотен запросов большинство
позиций обходятся без Pro-вызова: ~0,5–1 ₽ за запрос вместо 4.

Как. SQLite-таблица в том же catalog.db. Ключ — SHA-256 от нормализованного
текста позиции и ОТПЕЧАТКА схемы категории (имена атрибутов, типы, значения):
когда после ночного краула у категории меняется набор атрибутов, отпечаток
меняется, и старые ответы автоматически становятся промахами — отдельной
инвалидации не нужно. Значение — список ограничений как JSON. Кэшируются и
пустые ответы (модель решила, что ограничений нет): это тоже платный ответ.

Промах ничего не стоит и не мешает: маппер идёт в LLM как раньше и кладёт ответ
в кэш. Любая ошибка кэша — логируется и игнорируется, подбор не ломается.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Единый ключ для всех позиций одного запроса не подойдёт: клиенты комбинируют
# позиции по-разному. Кэшируем ПО ПОЗИЦИИ — так один и тот же «духовой шкаф
# чёрный 60 см» переиспользуется в любых комплектах.


def normalize_text(text: str) -> str:
    """Свести текст позиции к канонической форме: регистр, пробелы, ё/е, пунктуация.

    «Духовой шкаф, чёрный 60 см» и «духовой шкаф черный 60см» — одна и та же
    привязка, лишний промах здесь — лишние 1,2 ₽ за тысячу токенов.
    """
    t = (text or "").lower().replace("ё", "е")
    t = re.sub(r"(\d)[.,](\d)", r"\1DECPT\2", t)         # десятичные «36,6» сохраняем
    t = re.sub(r"[^\w\s%/\-]", " ", t)                   # прочая пунктуация → пробел
    t = t.replace("DECPT", ".")
    t = re.sub(r"(\d)\s+(см|мм|л|кг|дб)\b", r"\1\2", t)  # «60 см» → «60см»
    t = re.sub(r"\s+", " ", t).strip()
    return t


def schema_fingerprint(schema: dict[str, dict]) -> str:
    """Отпечаток схемы категории: имена, типы и значения атрибутов."""
    parts = []
    for name in sorted(schema):
        info = schema[name] or {}
        vals = info.get("values")
        if isinstance(vals, (list, tuple)):
            vals = sorted(map(str, vals))
        parts.append(f"{name}|{info.get('type')}|{info.get('min')}|{info.get('max')}|{vals}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def make_key(position_text: str, schema: dict[str, dict]) -> str:
    raw = normalize_text(position_text) + "\x1f" + schema_fingerprint(schema)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AttrMapCache:
    """Хранилище ответов схемо-маппера в catalog.db."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init(self) -> None:
        try:
            with self._conn() as con:
                con.execute(
                    """CREATE TABLE IF NOT EXISTS attr_map_cache (
                        key TEXT PRIMARY KEY,
                        text_norm TEXT NOT NULL,
                        schema_fp TEXT NOT NULL,
                        constraints_json TEXT NOT NULL,
                        hits INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL,
                        last_used_at REAL NOT NULL
                    )"""
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS ix_attr_map_cache_fp ON attr_map_cache(schema_fp)"
                )
        except sqlite3.Error:
            logger.warning("кэш привязки: не удалось создать таблицу", exc_info=True)

    def get(self, position_text: str, schema: dict[str, dict]) -> list[dict] | None:
        """Ограничения из кэша или None (промах). Пустой список — тоже попадание."""
        key = make_key(position_text, schema)
        try:
            with self._conn() as con:
                row = con.execute(
                    "SELECT constraints_json FROM attr_map_cache WHERE key=?", (key,)
                ).fetchone()
                if row is None:
                    return None
                con.execute(
                    "UPDATE attr_map_cache SET hits=hits+1, last_used_at=? WHERE key=?",
                    (time.time(), key),
                )
                return json.loads(row[0])
        except (sqlite3.Error, json.JSONDecodeError):
            logger.warning("кэш привязки: ошибка чтения", exc_info=True)
            return None

    def put(self, position_text: str, schema: dict[str, dict], constraints: list[dict]) -> None:
        key = make_key(position_text, schema)
        now = time.time()
        try:
            with self._conn() as con:
                con.execute(
                    """INSERT INTO attr_map_cache
                       (key, text_norm, schema_fp, constraints_json, hits, created_at, last_used_at)
                       VALUES (?,?,?,?,0,?,?)
                       ON CONFLICT(key) DO UPDATE SET
                         constraints_json=excluded.constraints_json,
                         last_used_at=excluded.last_used_at""",
                    (key, normalize_text(position_text), schema_fingerprint(schema),
                     json.dumps(constraints, ensure_ascii=False), now, now),
                )
        except sqlite3.Error:
            logger.warning("кэш привязки: ошибка записи", exc_info=True)

    def stats(self) -> dict:
        """Для /stats админа: размер кэша и сколько LLM-вызовов он сэкономил."""
        try:
            with self._conn() as con:
                n, hits = con.execute(
                    "SELECT COUNT(*), COALESCE(SUM(hits),0) FROM attr_map_cache"
                ).fetchone()
                return {"entries": int(n), "hits": int(hits)}
        except sqlite3.Error:
            return {"entries": 0, "hits": 0}

    def purge_stale(self, live_fingerprints: set[str]) -> int:
        """Удалить записи под схемы, которых больше нет (после краула). Возвращает число."""
        if not live_fingerprints:
            return 0
        try:
            with self._conn() as con:
                marks = ",".join("?" * len(live_fingerprints))
                cur = con.execute(
                    f"DELETE FROM attr_map_cache WHERE schema_fp NOT IN ({marks})",
                    tuple(live_fingerprints),
                )
                return cur.rowcount
        except sqlite3.Error:
            logger.warning("кэш привязки: ошибка очистки", exc_info=True)
            return 0
