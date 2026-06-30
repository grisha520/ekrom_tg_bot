"""Локальный кэш каталога ekrom (SQLite) — для масштаба под поток клиентов.

Краулер периодически складывает товары категорий в БД, а горячий путь подбора
читает кандидатов отсюда (мгновенно, параллельно, без браузера) вместо обхода
сайта Playwright'ом — это снимает главное узкое место.

Хранится по категориям (путь раздела каталога как ключ). Характеристики/фото
дозаписываются «на лету» при обогащении выбранного товара (write-through), так
что популярные позиции со временем отдаются из кэша целиком.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ..models import CatalogProduct


def _b2i(v: bool | None) -> int | None:
    return None if v is None else (1 if v else 0)


def _i2b(v: int | None) -> bool | None:
    return None if v is None else bool(v)


def _row_to_product(row) -> CatalogProduct:
    url, title, price, in_stock, image_url, attributes, product_id = row
    try:
        attrs = json.loads(attributes) if attributes else {}
    except Exception:  # noqa: BLE001
        attrs = {}
    return CatalogProduct(
        url=url, title=title, price=price, in_stock=_i2b(in_stock),
        image_url=image_url, attributes=attrs, product_id=product_id,
    )


class CatalogCache:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._ensure()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)

    def _ensure(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            # WAL: читатели (бот) не блокируются писателем (краул) — важно, чтобы
            # подбор работал во время ночного обновления кэша.
            c.execute("PRAGMA journal_mode=WAL")
            c.execute(
                """CREATE TABLE IF NOT EXISTS products (
                    url TEXT PRIMARY KEY,
                    category TEXT,
                    title TEXT,
                    price REAL,
                    in_stock INTEGER,
                    image_url TEXT,
                    attributes TEXT,
                    product_id TEXT,
                    updated_at REAL
                )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_category ON products(category)")

    def upsert_many(self, products: list[CatalogProduct], category: str) -> int:
        """Записать/обновить товары категории (из краулера). Не затирает уже
        накопленные характеристики/фото пустыми значениями."""
        now = time.time()
        rows = [
            (
                p.url, category, p.title, p.price, _b2i(p.in_stock), p.image_url,
                json.dumps(p.attributes or {}, ensure_ascii=False), p.product_id, now,
            )
            for p in products if p.url
        ]
        with self._conn() as c:
            c.executemany(
                """INSERT INTO products
                   (url,category,title,price,in_stock,image_url,attributes,product_id,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(url) DO UPDATE SET
                     category=excluded.category, title=excluded.title,
                     price=excluded.price, in_stock=excluded.in_stock,
                     image_url=COALESCE(excluded.image_url, products.image_url),
                     attributes=CASE WHEN excluded.attributes <> '{}'
                                     THEN excluded.attributes ELSE products.attributes END,
                     product_id=excluded.product_id, updated_at=excluded.updated_at""",
                rows,
            )
        return len(rows)

    def query_category(self, category: str) -> list[CatalogProduct]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT url,title,price,in_stock,image_url,attributes,product_id "
                "FROM products WHERE category=?",
                (category,),
            ).fetchall()
        return [_row_to_product(r) for r in rows]

    def category_age(self, category: str) -> float | None:
        """Сколько секунд назад категорию обновляли (None — её нет в кэше)."""
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(updated_at) FROM products WHERE category=?", (category,)
            ).fetchone()
        if not row or row[0] is None:
            return None
        return time.time() - row[0]

    def update_enrichment(
        self, url: str, attributes: dict[str, str], image_url: str | None
    ) -> None:
        """Дозаписать характеристики/фото товара (write-through после обогащения)."""
        if not url:
            return
        with self._conn() as c:
            c.execute(
                "UPDATE products SET attributes=?, "
                "image_url=COALESCE(?, image_url) WHERE url=?",
                (json.dumps(attributes or {}, ensure_ascii=False), image_url, url),
            )

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
