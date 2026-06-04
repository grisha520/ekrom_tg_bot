"""Playwright-клиент для ekrom.ru.

Отвечает за: поиск товаров, чтение карточек, добавление в корзину и
скачивание PDF из корзины. Использует сохранённую сессию (storage_state),
чтобы не проходить капчу при каждом запуске.

ВАЖНО: CSS-селекторы помечены TODO(этап-0) и должны быть подтверждены по
реальной разметке сайта (из разведки браузером). Логика потока — финальная,
точечно меняются только селекторы.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from ..config import Settings, get_settings
from ..models import CatalogProduct
from .catalog import build_search_url
from .session import storage_state_exists

# ── Селекторы (подтверждены реальным DOM ekrom) ──────────────────────────────
# Карточка товара в списке категории: .product-card с data-product-id.
SEL_PRODUCT_CARD = ".product-card"
SEL_PRODUCT_LINK = "a.product-card__name"
SEL_PRODUCT_PRICE = ".product-card__price"
SEL_PRODUCT_PRICE_OLD = ".product-card__price-old"
# В списке (карточка категории) — .js-product-add; на странице товара главная
# кнопка покупки — .js-detail-add (а .js-product-add там относится к блоку «похожие»).
SEL_ADD_TO_CART_CARD = ".js-product-add"
SEL_ADD_TO_CART = "button.js-detail-add"
SEL_CART_PDF_DOWNLOAD = "#print_order_button, .rect-btn.blue[data-bound-pdf='Y']"

# Форма входа (/auth/) — капчи нет
SEL_LOGIN_EMAIL = "input[placeholder*='email'], input[type='email'], input[name='USER_LOGIN']"
SEL_LOGIN_PASSWORD = "input[type='password']"
SEL_LOGIN_SUBMIT = "button:has-text('Войти')"


def _parse_price(text: str) -> float | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return float(digits) if digits else None


def _visible(selector: str) -> str:
    """Добавить псевдокласс :visible к каждому селектору в списке (через запятую)."""
    return ", ".join(f"{part.strip()}:visible" for part in selector.split(","))


class EkromClient:
    """Асинхронный клиент. Используйте как async context manager."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def __aenter__(self) -> "EkromClient":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def start(self) -> None:
        state_path = self._settings.storage_state_path
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=not self._settings.ekrom_headful)

        context_kwargs: dict = {"accept_downloads": True}
        if storage_state_exists(state_path):
            context_kwargs["storage_state"] = str(state_path)

        self._context = await self._browser.new_context(**context_kwargs)
        self._page = await self._context.new_page()
        # Bitrix использует нативный confirm() при удалении из корзины —
        # автоматически подтверждаем такие диалоги, иначе действие отменяется.
        self._page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("EkromClient не запущен — вызовите start()/используйте 'async with'")
        return self._page

    # ── Авторизация ────────────────────────────────────────────────────────────
    async def login(self, email: str | None = None, password: str | None = None) -> None:
        """Вход в кабинет (капчи нет). Учётка берётся из аргументов или .env.

        После входа сессию можно сохранить через save_storage_state(), чтобы
        переиспользовать cookies в headless-режиме.
        """
        email = email or self._settings.ekrom_login
        password = password or self._settings.ekrom_password
        if not email or not password:
            raise ValueError("EKROM_LOGIN/EKROM_PASSWORD не заданы — заполните .env")

        auth_url = f"{self._settings.ekrom_base_url.rstrip('/')}/auth/"
        await self.page.goto(auth_url, wait_until="domcontentloaded")

        # На странице может быть скрытая копия формы + видимая (в модалке).
        # Берём только ВИДИМОЕ поле email; если его нет — пробуем открыть модалку.
        email_field = self.page.locator(_visible(SEL_LOGIN_EMAIL)).first
        if await email_field.count() == 0:
            opener = self.page.get_by_role("button", name=re.compile(r"Войти|Вход", re.I)).first
            if await opener.count():
                await opener.click()
                await self.page.wait_for_timeout(800)
            email_field = self.page.locator(_visible(SEL_LOGIN_EMAIL)).first

        await email_field.fill(email)
        await self.page.locator(_visible(SEL_LOGIN_PASSWORD)).first.fill(password)
        await self.page.locator(_visible(SEL_LOGIN_SUBMIT)).first.click()

        # Признак успеха — попадание в личный кабинет
        try:
            await self.page.wait_for_url("**/personal/**", timeout=15000)
        except Exception:  # noqa: BLE001 — не критично, проверим по факту скачивания
            await self.page.wait_for_timeout(1500)

    async def save_storage_state(self) -> Path:
        """Сохранить текущие cookies сессии в storage_state-файл."""
        state_path = self._settings.storage_state_path
        state_path.parent.mkdir(parents=True, exist_ok=True)
        if self._context:
            await self._context.storage_state(path=str(state_path))
        return state_path

    # ── Каталог ────────────────────────────────────────────────────────────────
    async def browse_category(self, category_path: str, limit: int = 60) -> list[CatalogProduct]:
        """Прочитать товары со страницы категории (без фильтров)."""
        url = f"{self._settings.ekrom_base_url.rstrip('/')}{category_path}"
        return await self.browse_url(url, limit)

    async def browse_url(self, url: str, limit: int = 60) -> list[CatalogProduct]:
        """Перейти по готовому URL категории (возможно, с фильтрами) и собрать карточки.

        Поиск по сайту (/search/) карточки не отдаёт, поэтому работаем по URL категорий.
        """
        await self.page.goto(url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(1500)  # списки догружаются скриптом
        return await self._collect_cards(limit)

    async def search(self, query: str, limit: int = 20) -> list[CatalogProduct]:
        """Поиск по строке сайта (fallback). На ekrom отдаёт мало карточек —
        предпочтительнее browse_category()."""
        url = build_search_url(self._settings.ekrom_base_url, query)
        await self.page.goto(url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(1500)
        return await self._collect_cards(limit)

    async def _collect_cards(self, limit: int) -> list[CatalogProduct]:
        products: list[CatalogProduct] = []
        cards = self.page.locator(SEL_PRODUCT_CARD)
        count = min(await cards.count(), limit)
        for i in range(count):
            card = cards.nth(i)
            product_id = await card.get_attribute("data-product-id")

            title_el = card.locator(SEL_PRODUCT_LINK).first
            if not await title_el.count():
                continue
            title = (await title_el.inner_text()).strip()
            href = await title_el.get_attribute("href")

            price_el = card.locator(SEL_PRODUCT_PRICE).first
            price = _parse_price(await price_el.inner_text()) if await price_el.count() else None

            if not title or not href:
                continue
            products.append(
                CatalogProduct(
                    title=title,
                    url=self._abs_url(href),
                    price=price,
                    product_id=product_id,
                )
            )
        return products

    # ── Корзина ────────────────────────────────────────────────────────────────
    async def add_to_cart(self, product: CatalogProduct) -> bool:
        """Открыть страницу товара и нажать главную кнопку «Добавить в корзину».

        На странице товара главная кнопка — .js-detail-add (преселектится
        дефолтный оффер). Кнопки .js-product-add там относятся к «похожим».
        """
        await self.page.goto(product.url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(700)
        btn = self.page.locator(SEL_ADD_TO_CART).first
        if not await btn.count():
            return False
        try:
            await btn.scroll_into_view_if_needed(timeout=4000)
            await btn.click(timeout=8000)
        except Exception:  # noqa: BLE001 — возможно перекрыта; пробуем принудительно
            try:
                await btn.click(force=True, timeout=4000)
            except Exception:  # noqa: BLE001
                return False
        await self.page.wait_for_timeout(1200)  # дать корзине обновиться
        return True

    async def clear_cart(self) -> None:
        """Очистить корзину: «Выбрать все» → «Удалить выбранные», в цикле до пустой.

        На странице корзины нет одиночной кнопки «Очистить»; надёжный путь —
        выделить все позиции и удалить выбранные, повторяя при необходимости.
        """
        cart_url = f"{self._settings.ekrom_base_url.rstrip('/')}/personal/cart/"
        await self.page.goto(cart_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(1500)

        for _ in range(8):
            select_all = self.page.locator(".basket-select-all-btn").first
            del_btn = self.page.locator(".basket-delete-selected-btn").first
            # Кнопки удаления нет/не видна → корзина пуста
            if not (await del_btn.count() and await del_btn.is_visible()):
                break

            try:
                if await select_all.count():
                    await select_all.click(timeout=4000)
                    await self.page.wait_for_timeout(500)
                await del_btn.click(timeout=5000)
            except Exception:  # noqa: BLE001
                break

            await self.page.wait_for_timeout(1000)
            # возможное подтверждение в модалке
            confirm = self.page.get_by_role("button", name=re.compile(r"Да|Удалить|Очистить", re.I)).first
            if await confirm.count():
                try:
                    await confirm.click(timeout=2000)
                except Exception:  # noqa: BLE001
                    pass
            await self.page.wait_for_timeout(1500)
            await self.page.reload(wait_until="domcontentloaded")
            await self.page.wait_for_timeout(1500)

    async def download_cart_pdf(self, out_path: Path) -> Path:
        """Перейти в корзину и скачать PDF со списком товаров.

        Требует авторизованной сессии (storage_state). Возвращает путь к файлу.
        """
        # Подтверждено: /personal/cart/ редиректит на /personal/order/make/
        cart_url = f"{self._settings.ekrom_base_url.rstrip('/')}/personal/cart/"
        await self.page.goto(cart_url, wait_until="domcontentloaded")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        async with self.page.expect_download() as dl_info:
            await self.page.locator(SEL_CART_PDF_DOWNLOAD).first.click()
        download = await dl_info.value
        await download.save_as(str(out_path))
        return out_path

    # ── Вспомогательное ─────────────────────────────────────────────────────────
    def _abs_url(self, href: str) -> str:
        if href.startswith("http"):
            return href
        return f"{self._settings.ekrom_base_url.rstrip('/')}/{href.lstrip('/')}"
