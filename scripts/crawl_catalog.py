"""Наполнить кэш каталога ekrom (запускать периодически).

  python scripts/crawl_catalog.py

Кэш лежит в data/catalog.db. Горячий путь подбора читает кандидатов оттуда,
если USE_CATALOG_CACHE=true и кэш свежий (CATALOG_CACHE_TTL).
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from max_ekrom_bot.config import get_settings
from max_ekrom_bot.ekrom.catalog_cache import CatalogCache
from max_ekrom_bot.ekrom.crawler import crawl


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-images", action="store_true",
                    help="не проверять и не добирать фото товаров")
    ap.add_argument("--image-limit", type=int, default=None,
                    help="сколько карточек открывать ради фото за прогон")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx пишет строку на КАЖДЫЙ запрос: с проверкой фото это ~5 000 строк за
    # ночь, а ротации у логов нет. Оставляем только предупреждения и ошибки.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    results = asyncio.run(crawl(
        refresh_images=not args.no_images, image_limit=args.image_limit,
    ))
    print("\n=== Итог краула ===")
    for path, n in results.items():
        print(f"{n:5d}  {path}")
    cache = CatalogCache(get_settings().catalog_db_path)
    print(f"всего товаров в кэше: {cache.count()}")
    print(f"с характеристиками:  {cache.attributes_count()}")
    print(f"с фото:              {len(cache.image_pairs())}")


if __name__ == "__main__":
    main()
