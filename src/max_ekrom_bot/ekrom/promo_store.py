"""Хранилище кампаний раздела «Акции» (SQLite) — датасет комплектных акций.

Кладём разобранные краулером кампании (`promos.crawl_promos`) и их витринных
участников в те же файлы, что и каталог (по умолчанию `catalog.db`), чтобы
`promo_items.url`/`product_id` соединялись с `products` при расчёте комплекта
(Ф2/Ф4). Кампании обновляются целиком (пересобираются при каждом прогоне краула
акций), участники кампании — тоже.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .promos import PromoCampaign, PromoItem


class PromoStore:
    """Датасет акций: таблицы `promo_campaigns` и `promo_items`."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._ensure()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)

    def _ensure(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute(
                """CREATE TABLE IF NOT EXISTS promo_campaigns (
                    slug TEXT PRIMARY KEY,
                    url TEXT,
                    title TEXT,
                    brand TEXT,
                    mechanic TEXT,
                    period_from TEXT,
                    period_to TEXT,
                    scope TEXT,
                    requires_oven INTEGER,
                    tiers TEXT,
                    rules_text TEXT,
                    updated_at REAL
                )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS promo_items (
                    slug TEXT,
                    url TEXT,
                    product_id TEXT,
                    brand TEXT,
                    title TEXT,
                    price REAL,
                    old_price REAL,
                    PRIMARY KEY (slug, url)
                )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_promo_brand ON promo_campaigns(brand)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_promo_items_slug ON promo_items(slug)")

    def replace_all(self, campaigns: list[PromoCampaign]) -> int:
        """Полностью пересобрать датасет акций из свежего прогона краула."""
        now = time.time()
        with self._conn() as c:
            c.execute("DELETE FROM promo_campaigns")
            c.execute("DELETE FROM promo_items")
            c.executemany(
                """INSERT INTO promo_campaigns
                   (slug,url,title,brand,mechanic,period_from,period_to,scope,
                    requires_oven,tiers,rules_text,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        camp.slug, camp.url, camp.title, camp.brand, camp.mechanic,
                        camp.period_from, camp.period_to, camp.scope,
                        1 if camp.requires_oven else 0,
                        json.dumps({str(k): v for k, v in camp.tiers.items()}, ensure_ascii=False),
                        camp.rules_text, now,
                    )
                    for camp in campaigns
                ],
            )
            item_rows = [
                (camp.slug, it.url, it.product_id, it.brand, it.title, it.price, it.old_price)
                for camp in campaigns
                for it in camp.items
            ]
            c.executemany(
                """INSERT OR REPLACE INTO promo_items
                   (slug,url,product_id,brand,title,price,old_price)
                   VALUES (?,?,?,?,?,?,?)""",
                item_rows,
            )
        return len(campaigns)

    def campaigns_for_brand(self, brand: str) -> list[dict]:
        """Кампании бренда (строки как dict) — для расчёта комплекта."""
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT * FROM promo_campaigns WHERE brand=? COLLATE NOCASE "
                "ORDER BY mechanic, slug",
                (brand,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tiers"] = {int(k): v for k, v in json.loads(d.get("tiers") or "{}").items()}
            d["requires_oven"] = bool(d.get("requires_oven"))
            out.append(d)
        return out

    def brand_counts(self) -> dict[str, int]:
        """Сколько кампаний на каждый бренд (для отчёта датасета)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT COALESCE(brand,'—') b, COUNT(*) n FROM promo_campaigns GROUP BY b"
            ).fetchall()
        return {b: n for b, n in rows}

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM promo_campaigns").fetchone()[0]
