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
    _purge_attr_map_cache(cache, results)


def _purge_attr_map_cache(cache: CatalogCache, results: dict[str, int]) -> None:
    """Убрать из кэша привязки записи под схемы, которых после краула больше нет.

    Ключ кэша включает отпечаток схемы категории, поэтому устаревшие записи и так
    не срабатывают — но без чистки они копились бы бесконечно.
    """
    try:
        from max_ekrom_bot.matching.attr_schema import build_category_schema
        from max_ekrom_bot.parsing.attr_map_cache import AttrMapCache, schema_fingerprint

        live = {
            schema_fingerprint(build_category_schema(cache.query_category(path)))
            for path in results
        }
        removed = AttrMapCache(get_settings().catalog_db_path).purge_stale(live)
        print(f"кэш привязки характеристик: удалено устаревших записей — {removed}")
    except Exception as exc:  # noqa: BLE001 — чистка кэша не должна валить краул
        print(f"кэш привязки характеристик: чистка пропущена ({exc!r})")


if __name__ == "__main__":
    main()
