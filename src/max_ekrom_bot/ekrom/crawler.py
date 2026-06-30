"""Краулер каталога ekrom → локальный кэш (для масштаба).

Проходит 6 базовых категорий и складывает листинги (название/url/цена/наличие)
в CatalogCache. Запускается периодически (cron/systemd-timer), горячий путь
подбора затем читает кандидатов из кэша без браузера.

Характеристики и фото тут не вытягиваем (это дорого — страница на товар); они
дозаписываются «на лету» при обогащении выбранного товара в подборе.
"""

from __future__ import annotations

import logging

from ..config import Settings, get_settings
from .catalog import CATEGORY_URLS
from .catalog_cache import CatalogCache
from .client import EkromClient

logger = logging.getLogger(__name__)

# Сколько карточек собирать с категории (как в живом обходе — одна страница).
_LIMIT = 200


async def crawl(settings: Settings | None = None) -> dict[str, int]:
    """Наполнить кэш каталога. Возвращает {путь_категории: число_товаров}."""
    settings = settings or get_settings()
    cache = CatalogCache(settings.catalog_db_path)
    results: dict[str, int] = {}
    async with EkromClient(settings) as ek:
        for appliance, path in CATEGORY_URLS.items():
            try:
                products = await ek.browse_category(path, limit=_LIMIT)
            except Exception:  # noqa: BLE001 — одна категория не должна валить краул
                logger.exception("ошибка обхода категории %s", path)
                products = []
            n = cache.upsert_many(products, path) if products else 0
            results[path] = n
            logger.info("категория %s: %s товаров", path, n)
    return results
