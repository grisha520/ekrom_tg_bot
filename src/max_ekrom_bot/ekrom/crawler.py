"""Краулер каталога ekrom → локальный кэш (для масштаба).

Проходит ВСЕ категории каталога, складывает листинги (название/url/цена/наличие)
в CatalogCache, затем ОБОГАЩАЕТ товары полными характеристиками и фото (страница
на товар). Запускается периодически (cron); горячий путь подбора читает готовых
кандидатов с характеристиками из кэша без браузера — быстро и параллельно.

Обогащение инкрементальное: уже обогащённые товары при повторных прогонах не
трогаем, поэтому первый прогон долгий (~часы), последующие — быстрые.
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
_ENRICH_CONCURRENCY = 4  # сколько карточек товаров обогащаем параллельно


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


def _all_category_paths() -> list[str]:
    """Все категории для краула: 6 базовых типов + весь каталог из categories.json."""
    paths = list(CATEGORY_URLS.values())
    seen = set(paths)
    try:
        from ..parsing.category_resolver import load_categories

        for cat in load_categories():
            url = (cat.get("url") or "").strip()
            if url and url not in seen:
                seen.add(url)
                paths.append(url)
    except Exception:  # noqa: BLE001 — без categories.json краулим хотя бы базовые
        logger.exception("не удалось прочитать список категорий — краулю только базовые")
    return paths


async def _enrich_missing(
    ek: EkromClient, cache: CatalogCache, limit: int | None = None
) -> tuple[int, int]:
    """Дочитать характеристики+фото для товаров, у которых их ещё нет.

    Возвращает (обогащено, всего_кандидатов). Инкрементально: берём только
    товары без характеристик, поэтому повторные прогоны дешёвые.
    """
    urls = cache.urls_without_attributes()
    if limit is not None:
        urls = urls[:limit]
    total = len(urls)
    if not total:
        return 0, 0
    logger.info("обогащение: товаров без характеристик — %s", total)
    sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)
    done = 0

    async def one(url: str) -> bool:
        async with sem:
            pg = await ek.new_page()
            try:
                await pg.goto(url, wait_until="domcontentloaded")
                await pg.wait_for_timeout(400)
                props = await ek.read_product_properties(pg)
                img = await ek.read_product_image(pg)
                if props or img:
                    cache.update_enrichment(url, props, img)
                return bool(props)
            except Exception:  # noqa: BLE001 — один товар не валит обогащение
                return False
            finally:
                await pg.close()

    # Прогресс порциями, чтобы в логах было видно движение на длинном прогоне.
    chunk = 200
    for i in range(0, total, chunk):
        batch = urls[i : i + chunk]
        res = await asyncio.gather(*(one(u) for u in batch))
        done += sum(res)
        logger.info("обогащение: %s / %s (успешно %s)", min(i + chunk, total), total, done)
    return done, total


async def crawl(
    settings: Settings | None = None, enrich: bool = True, enrich_limit: int | None = None
) -> dict[str, int]:
    """Наполнить кэш каталога (все категории, все страницы) и обогатить товары.

    enrich=True — дочитать характеристики+фото у товаров без них (инкрементально).
    enrich_limit — ограничить число обогащаемых за прогон (для дробления нагрузки).
    """
    settings = settings or get_settings()
    base = settings.ekrom_base_url.rstrip("/")
    cache = CatalogCache(settings.catalog_db_path)
    paths = _all_category_paths()
    logger.info("категорий к обходу: %s", len(paths))
    results: dict[str, int] = {}
    async with EkromClient(settings) as ek:
        for path in paths:
            try:
                products = await crawl_category(ek, base, path)
            except Exception:  # noqa: BLE001 — одна категория не должна валить краул
                logger.exception("ошибка обхода категории %s", path)
                products = []
            n = cache.upsert_many(products, path) if products else 0
            results[path] = n
            logger.info("категория %s: %s товаров", path, n)
        if enrich:
            await _enrich_missing(ek, cache, limit=enrich_limit)
    return results
