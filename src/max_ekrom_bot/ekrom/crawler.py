"""Краулер каталога ekrom → локальный кэш (для масштаба).

Проходит 6 базовых категорий и складывает листинги (название/url/цена/наличие)
в CatalogCache. Запускается периодически (cron/systemd-timer), горячий путь
подбора затем читает кандидатов из кэша без браузера.

Характеристики и фото тут не вытягиваем (это дорого — страница на товар); они
дозаписываются «на лету» при обогащении выбранного товара в подборе.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import Settings, get_settings
from .catalog import CATEGORY_URLS
from .catalog_cache import CatalogCache
from .client import EkromClient, product_slug

logger = logging.getLogger(__name__)

_PER_PAGE_LIMIT = 500   # все карточки страницы (реально на странице ~21)
_MAX_PAGES = 80         # предохранитель от рантового обхода
_PAGE_CONCURRENCY = 4   # сколько страниц грузим параллельно (на 2 ядра/4ГБ ок)


async def _read_max_page(page) -> int:
    """Номер последней страницы из ссылок пагинации (?PAGEN_1=N)."""
    try:
        n = await page.evaluate(
            "() => { const ns = [...document.querySelectorAll('a[href*=\"PAGEN_1=\"]')]"
            ".map(a => { const m=(a.getAttribute('href')||'').match(/PAGEN_1=(\\d+)/);"
            " return m ? +m[1] : 0; }); return ns.length ? Math.max(...ns) : 1; }"
        )
    except Exception:  # noqa: BLE001
        return 1
    return max(1, min(int(n or 1), _MAX_PAGES))


async def crawl_category(ek: EkromClient, base: str, path: str) -> list:
    """Собрать ВСЕ страницы категории (с пагинацией), дедуплицируя по slug."""
    url0 = f"{base}{path}"
    first = await ek.browse_url(url0, limit=_PER_PAGE_LIMIT)
    products: dict[str, object] = {product_slug(p.url): p for p in first}
    max_page = await _read_max_page(ek.page)
    if max_page <= 1:
        return list(products.values())

    sem = asyncio.Semaphore(_PAGE_CONCURRENCY)

    async def fetch(n: int) -> list:
        async with sem:
            pg = await ek.new_page()
            try:
                return await ek.browse_url(f"{url0}?PAGEN_1={n}", limit=_PER_PAGE_LIMIT, page=pg)
            except Exception:  # noqa: BLE001 — одна страница не валит категорию
                return []
            finally:
                await pg.close()

    pages = await asyncio.gather(*(fetch(n) for n in range(2, max_page + 1)))
    for lst in pages:
        for p in lst:
            products.setdefault(product_slug(p.url), p)
    return list(products.values())


async def crawl(settings: Settings | None = None) -> dict[str, int]:
    """Наполнить кэш каталога (все страницы категорий). {путь: число_товаров}."""
    settings = settings or get_settings()
    base = settings.ekrom_base_url.rstrip("/")
    cache = CatalogCache(settings.catalog_db_path)
    results: dict[str, int] = {}
    async with EkromClient(settings) as ek:
        for _appliance, path in CATEGORY_URLS.items():
            try:
                products = await crawl_category(ek, base, path)
            except Exception:  # noqa: BLE001 — одна категория не должна валить краул
                logger.exception("ошибка обхода категории %s", path)
                products = []
            n = cache.upsert_many(products, path) if products else 0
            results[path] = n
            logger.info("категория %s: %s товаров", path, n)
    return results
