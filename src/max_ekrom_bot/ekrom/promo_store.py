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
from datetime import date
from pathlib import Path

from .promos import GiftRule, PromoCampaign, PromoItem, SpecPriceRule


def _gift_json(gift: GiftRule | None) -> str | None:
    """Сериализовать оцифрованное условие подарка (или None)."""
    if gift is None:
        return None
    return json.dumps(
        {"condition": gift.condition, "options": {str(k): v for k, v in gift.options.items()}},
        ensure_ascii=False,
    )


def _gift_from_json(raw: str | None) -> dict | None:
    """Разобрать gift_json обратно в {condition, options:{int:model}} (или None)."""
    if not raw:
        return None
    d = json.loads(raw)
    return {"condition": d.get("condition"),
            "options": {int(k): v for k, v in (d.get("options") or {}).items()}}


def _spec_json(spec: SpecPriceRule | None) -> str | None:
    """Сериализовать правило «доп. предмет по спеццене» (или None)."""
    if spec is None:
        return None
    return json.dumps(
        {"models": spec.models, "tiers": {str(k): v for k, v in spec.tiers.items()},
         "pct": spec.pct, "line_label": spec.line_label},
        ensure_ascii=False,
    )


def _spec_from_json(raw: str | None) -> dict | None:
    """Разобрать spec_json обратно в {models, tiers:{int:цена}, pct, line_label}."""
    if not raw:
        return None
    d = json.loads(raw)
    return {"models": d.get("models") or [],
            "tiers": {int(k): v for k, v in (d.get("tiers") or {}).items()},
            "pct": d.get("pct"), "line_label": d.get("line_label")}


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
                    gift_json TEXT,
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
            # Миграция: новые столбцы могли отсутствовать в БД прошлой версии
            # (CREATE IF NOT EXISTS схему не меняет) — добавляем на месте.
            cols = {r[1] for r in c.execute("PRAGMA table_info(promo_campaigns)")}
            for col in ("gift_json", "spec_json", "exclusions_json"):
                if col not in cols:
                    c.execute(f"ALTER TABLE promo_campaigns ADD COLUMN {col} TEXT")
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
                    requires_oven,tiers,gift_json,spec_json,exclusions_json,
                    rules_text,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        camp.slug, camp.url, camp.title, camp.brand, camp.mechanic,
                        camp.period_from, camp.period_to, camp.scope,
                        1 if camp.requires_oven else 0,
                        json.dumps({str(k): v for k, v in camp.tiers.items()}, ensure_ascii=False),
                        _gift_json(camp.gift),
                        _spec_json(camp.spec),
                        json.dumps(camp.exclusions, ensure_ascii=False) if camp.exclusions else None,
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

    def campaigns_for_brand(self, brand: str, today: date | None = None) -> list[dict]:
        """ДЕЙСТВУЮЩИЕ кампании бренда (строки как dict) — для расчёта комплекта.

        Истёкшие (`period_to` раньше `today`) и ещё не начавшиеся (`period_from`
        позже `today`) кампании отсекаются: датасет обновляется краулом раз в
        сутки, и между прогонами акция может закончиться — обещать клиенту
        скидку по ней нельзя. Кампании без распознанных дат считаем действующими
        (их срок был актуален на момент краула).
        """
        iso_today = (today or date.today()).isoformat()
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT * FROM promo_campaigns WHERE brand=? COLLATE NOCASE "
                "AND (period_to IS NULL OR period_to >= ?) "
                "AND (period_from IS NULL OR period_from <= ?) "
                "ORDER BY mechanic, slug",
                (brand, iso_today, iso_today),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tiers"] = {int(k): v for k, v in json.loads(d.get("tiers") or "{}").items()}
            d["requires_oven"] = bool(d.get("requires_oven"))
            d["gift"] = _gift_from_json(d.get("gift_json"))
            d["spec"] = _spec_from_json(d.get("spec_json"))
            d["exclusions"] = json.loads(d.get("exclusions_json") or "[]")
            d["listed_urls"] = self._listed_urls(d["slug"]) if d.get("scope") == "listed" else []
            out.append(d)
        return out

    def _listed_urls(self, slug: str) -> list[str]:
        """URL витринных участников кампании (для проверки scope=listed)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT url FROM promo_items WHERE slug=?", (slug,)
            ).fetchall()
        return [u for (u,) in rows if u]

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
