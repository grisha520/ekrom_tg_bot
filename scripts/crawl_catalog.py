"""Наполнить кэш каталога ekrom (запускать периодически).

  python scripts/crawl_catalog.py

Кэш лежит в data/catalog.db. Горячий путь подбора читает кандидатов оттуда,
если USE_CATALOG_CACHE=true и кэш свежий (CATALOG_CACHE_TTL).
"""

from __future__ import annotations

import asyncio
import logging

from max_ekrom_bot.config import get_settings
from max_ekrom_bot.ekrom.catalog_cache import CatalogCache
from max_ekrom_bot.ekrom.crawler import crawl


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    results = asyncio.run(crawl())
    print("\n=== Итог краула ===")
    for path, n in results.items():
        print(f"{n:5d}  {path}")
    cache = CatalogCache(get_settings().catalog_db_path)
    print(f"всего товаров в кэше: {cache.count()}")
    print(f"с характеристиками:  {cache.attributes_count()}")


if __name__ == "__main__":
    main()
