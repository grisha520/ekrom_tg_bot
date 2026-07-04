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
import html as _html
import json
import logging
import re

import httpx

from ..config import Settings, get_settings
from .catalog import CATEGORY_URLS
from .catalog_cache import CatalogCache
from .client import EkromClient, product_slug

logger = logging.getLogger(__name__)

_PER_PAGE_LIMIT = 500   # все карточки страницы (реально на странице ~21)
_MAX_PAGES = 80         # предохранитель от рантового обхода
_PAGE_CONCURRENCY = 4   # сколько страниц грузим параллельно (на 2 ядра/4ГБ ок)
# Обогащение характеристик — через httpx (без браузера): страницы ekrom рендерятся
# на сервере, характеристики уже в HTML. httpx лёгкий, но ekrom анти-ботит бурст —
# держим параллель умеренной и шлём браузерные заголовки + куки сессии, иначе 403.
_ENRICH_HTTP_CONCURRENCY = 5
_ENRICH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


def _ekrom_cookies(settings: Settings) -> dict[str, str]:
    """Куки сессии ekrom из storage_state — httpx без них ловит 403 от анти-бота."""
    try:
        st = json.loads(settings.storage_state_path.read_text(encoding="utf-8"))
        return {
            c["name"]: c["value"]
            for c in st.get("cookies", [])
            if "ekrom" in (c.get("domain") or "")
        }
    except Exception:  # noqa: BLE001 — нет сессии → пробуем без куки
        return {}
_RE_PROP_NAME = re.compile(r"product-info__property-name[^>]*>(.*?)</", re.S)
_RE_PROP_VALUE = re.compile(r"product-info__property-value[^>]*>(.*?)</", re.S)
_RE_TAG = re.compile(r"<[^>]+>")


def _parse_attributes(html_text: str) -> dict[str, str]:
    """Достать пары характеристик из сырого HTML карточки товара ekrom."""
    names = _RE_PROP_NAME.findall(html_text)
    values = _RE_PROP_VALUE.findall(html_text)
    attrs: dict[str, str] = {}
    for name, val in zip(names, values):
        n = _html.unescape(_RE_TAG.sub("", name)).strip()
        v = _html.unescape(_RE_TAG.sub("", val)).replace("\n", " ").strip()
        if n and v and n not in attrs:
            attrs[n] = v
    return attrs


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
    settings: Settings, cache: CatalogCache, limit: int | None = None
) -> tuple[int, int]:
    """Дочитать характеристики для товаров без них — через httpx (без браузера).

    Возвращает (обогащено, всего). Инкрементально: берём только товары без
    характеристик. Фото тут НЕ трогаем — оно проставляется в подборе (Playwright,
    выбор по размеру) для выбранного товара; здесь важен полный набор характеристик.
    """
    urls = cache.urls_without_attributes()
    if limit is not None:
        urls = urls[:limit]
    total = len(urls)
    if not total:
        return 0, 0
    logger.info("обогащение (http): товаров без характеристик — %s", total)
    sem = asyncio.Semaphore(_ENRICH_HTTP_CONCURRENCY)
    done = 0

    async with httpx.AsyncClient(
        timeout=25.0, follow_redirects=True, headers=_ENRICH_HEADERS,
        cookies=_ekrom_cookies(settings),
    ) as client:

        async def one(url: str) -> bool:
            async with sem:
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        return False
                    attrs = _parse_attributes(r.text)
                    if attrs:
                        cache.update_enrichment(url, attrs, None)
                        return True
                except Exception:  # noqa: BLE001 — один товар не валит обогащение
                    return False
                return False

        chunk = 300
        for i in range(0, total, chunk):
            res = await asyncio.gather(*(one(u) for u in urls[i : i + chunk]))
            done += sum(res)
            logger.info(
                "обогащение: %s / %s (успешно %s)", min(i + chunk, total), total, done
            )
    return done, total


async def crawl(
    settings: Settings | None = None, enrich: bool = True, enrich_limit: int | None = None
) -> dict[str, int]:
    """Наполнить кэш каталога (все категории, все страницы) и обогатить товары.

    enrich=True — дочитать характеристики у товаров без них (инкрементально, httpx).
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
    # Обогащение — вне браузера (httpx), после сбора листингов.
    if enrich:
        await _enrich_missing(settings, cache, limit=enrich_limit)
    return results
