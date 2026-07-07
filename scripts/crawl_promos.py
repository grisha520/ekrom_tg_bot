"""Собрать датасет акций ekrom (раздел «Акции») и сложить в promo-таблицы.

  python scripts/crawl_promos.py

Датасет лежит рядом с каталогом (data/catalog.db, таблицы promo_campaigns /
promo_items). Движок комплекта (Ф2/Ф4) читает механику и правила кампаний отсюда.
"""

from __future__ import annotations

import asyncio
import logging

from max_ekrom_bot.config import get_settings
from max_ekrom_bot.ekrom.promo_store import PromoStore
from max_ekrom_bot.ekrom.promos import crawl_promos


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    campaigns = asyncio.run(crawl_promos(settings, target_brands_only=True))

    store = PromoStore(settings.catalog_db_path)
    store.replace_all(campaigns)

    print("\n=== Датасет акций (5 брендов) ===")
    for camp in sorted(campaigns, key=lambda c: (c.brand or "", c.mechanic)):
        period = " → ".join(filter(None, [camp.period_from, camp.period_to])) or "срок ?"
        tiers = ", ".join(f"{n}→{p:g}%" for n, p in sorted(camp.tiers.items())) or "—"
        print(
            f"{camp.brand or '—':12} {camp.mechanic:16} [{period}] "
            f"поз.={len(camp.items):2}  сетка: {tiers}"
        )
        print(f"             {camp.slug}  ({camp.title})")

    print("\nкампаний на бренд:", store.brand_counts())
    print("всего кампаний в датасете:", store.count())
    # Явно отметим бренды без единой акции — это влияет на охват проекта.
    from max_ekrom_bot.ekrom.promos import TARGET_BRANDS, _BRAND_DISPLAY
    present = {(c.brand or "").lower() for c in campaigns}
    missing = [_BRAND_DISPLAY[b] for b in TARGET_BRANDS if b not in present]
    if missing:
        print("БРЕНДЫ БЕЗ АКЦИЙ:", ", ".join(missing))


if __name__ == "__main__":
    main()
