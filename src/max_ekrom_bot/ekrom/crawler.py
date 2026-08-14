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


# Главное фото карточки: первый <img class="lazyload"> галереи товара. Иконки меню
# идут без этого класса и в .png, похожие товары — в блоке similar__img-box ниже,
# поэтому первое совпадение — именно фото товара (сверено на живых карточках).
_RE_MAIN_IMAGE = re.compile(
    r'<img[^>]+class="[^"]*lazyload[^"]*"[^>]+src="(/upload/iblock/[^"]+\.'
    r'(?:jpg|jpeg|png|webp))"',
    re.I,
)
_RE_ANY_IMAGE = re.compile(r'src="(/upload/iblock/[^"]+\.(?:jpg|jpeg|webp))"', re.I)

# Пять брендов проекта: их фото добираем в первую очередь.
_IMAGE_PRIORITY_BRANDS = ("korting", "maunfeld", "midea", "lex", "kuppersberg")
# Сколько карточек открывать за прогон ради фото (ночной джоб не должен идти часами).
_IMAGE_FETCH_LIMIT = 2500
_IMAGE_HEAD_CONCURRENCY = 10


def _parse_image(html_text: str) -> str | None:
    """Ссылка на главное фото товара из сырого HTML карточки (или None)."""
    m = _RE_MAIN_IMAGE.search(html_text) or _RE_ANY_IMAGE.search(html_text)
    return m.group(1) if m else None


async def _refresh_images(
    settings: Settings, cache: CatalogCache, limit: int | None = None
) -> tuple[int, int, int]:
    """Починить протухшие ссылки на фото и добрать недостающие.

    Зачем: Bitrix перегенерирует пути в /upload/iblock/, и сохранённая ссылка со
    временем начинает отдавать 404-страницу — в клиентском PDF на её месте пустое
    поле. Сам товар при этом живой, поэтому заметить проблему можно только
    проверкой ссылки.

    Порядок: сначала дешёвый HEAD по всем известным фото (страницы не грузим),
    затем открываем карточки только там, где ссылка мертва или фото не было
    вовсе. Возвращает (починено, добрано, проверено).
    """
    limit = _IMAGE_FETCH_LIMIT if limit is None else limit
    pairs = cache.image_pairs()
    base = settings.ekrom_base_url.rstrip("/")
    dead: list[str] = []

    async with httpx.AsyncClient(
        timeout=20.0, follow_redirects=True, headers=_ENRICH_HEADERS,
        cookies=_ekrom_cookies(settings),
    ) as client:
        head_sem = asyncio.Semaphore(_IMAGE_HEAD_CONCURRENCY)

        async def alive(product_url: str, image_url: str) -> None:
            async with head_sem:
                try:
                    r = await client.head(image_url)
                    ok = (r.status_code == 200
                          and "image" in (r.headers.get("content-type") or ""))
                except Exception:  # noqa: BLE001 — сеть моргнула: не считаем мёртвой,
                    return         # иначе один сбой погонит перечитывать весь каталог
                if not ok:
                    dead.append(product_url)

        logger.info("фото: проверяю %s ссылок", len(pairs))
        for i in range(0, len(pairs), 500):
            await asyncio.gather(*(alive(u, img) for u, img in pairs[i : i + 500]))
        logger.info("фото: протухших ссылок — %s", len(dead))

        missing = cache.urls_missing_image(_IMAGE_PRIORITY_BRANDS)
        todo = dead + [u for u in missing if u not in set(dead)]
        todo = todo[:limit]
        logger.info("фото: открою карточек — %s (без фото в базе: %s)",
                    len(todo), len(missing))

        fetch_sem = asyncio.Semaphore(_ENRICH_HTTP_CONCURRENCY)
        fixed = filled = 0
        dead_set = set(dead)

        async def one(url: str) -> str | None:
            async with fetch_sem:
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        return None
                    src = _parse_image(r.text)
                except Exception:  # noqa: BLE001 — один товар не валит прогон
                    return None
                return f"{base}{src}" if src else None

        chunk = 200
        for i in range(0, len(todo), chunk):
            batch = todo[i : i + chunk]
            for url, img in zip(batch, await asyncio.gather(*(one(u) for u in batch))):
                if not img:
                    continue
                cache.update_image(url, img)
                if url in dead_set:
                    fixed += 1
                else:
                    filled += 1
            logger.info("фото: %s / %s (починено %s, добрано %s)",
                        min(i + chunk, len(todo)), len(todo), fixed, filled)
    return fixed, filled, len(pairs)


async def crawl(
    settings: Settings | None = None, enrich: bool = True, enrich_limit: int | None = None,
    refresh_images: bool = True, image_limit: int | None = None,
) -> dict[str, int]:
    """Наполнить кэш каталога (все категории, все страницы) и обогатить товары.

    enrich=True — дочитать характеристики у товаров без них (инкрементально, httpx).
    enrich_limit — ограничить число обогащаемых за прогон (для дробления нагрузки).
    refresh_images=True — проверить ссылки на фото и починить протухшие/добрать
    недостающие; image_limit — сколько карточек открывать ради фото за прогон.
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
    if refresh_images:
        try:
            fixed, filled, checked = await _refresh_images(settings, cache, image_limit)
            logger.info("фото: проверено %s, починено %s, добрано %s",
                        checked, fixed, filled)
        except Exception:  # noqa: BLE001 — фото не критичнее самого каталога
            logger.exception("обновление фото не удалось — каталог всё равно собран")
    return results
