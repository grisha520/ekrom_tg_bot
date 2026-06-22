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
# В списке (карточка категории) — .js-product-add.
SEL_ADD_TO_CART_CARD = ".js-product-add"
# На странице товара главная кнопка «В корзину» — это видимая rect-кнопка в
# основном блоке товара (.js-detail). Раньше целились в button.js-detail-add,
# но на части карточек она скрыта (есть видимый дубль .js-product-add), из-за
# чего товар не добавлялся и PDF выходил неполным. Берём ПЕРВУЮ ВИДИМУЮ.
SEL_ADD_TO_CART = (
    ".js-detail button.js-detail-add:visible, "
    ".js-detail .rect-btn.js-detail-add:visible, "
    "button.js-detail-add:visible"
)
SEL_CART_PDF_DOWNLOAD = "#print_order_button, .rect-btn.blue[data-bound-pdf='Y']"

# Статус наличия (подтверждено по DOM ekrom): на странице товара —
# .product-box__status, в карточке каталога — .product-info__stock-status.
SEL_PRODUCT_STATUS = (
    ".product-box__status, .product-card__status, .product-info__stock-status"
)
SEL_PRODUCT_OUT_OF_STOCK = ".product-box__out-of-stock.show, .product-card__out-of-stock.show"

# Форма входа (/auth/) — капчи нет
SEL_LOGIN_EMAIL = "input[placeholder*='email'], input[type='email'], input[name='USER_LOGIN']"
SEL_LOGIN_PASSWORD = "input[type='password']"
SEL_LOGIN_SUBMIT = "button:has-text('Войти')"


class EkromAuthError(RuntimeError):
    """Сессия ekrom истекла или отсутствует — требуется повторный вход.

    Поднимается перед операциями, которым нужна авторизация (корзина, PDF),
    чтобы показать менеджеру понятное сообщение вместо общей ошибки.
    """


class EkromCartError(RuntimeError):
    """Не удалось добавить часть товаров в корзину (например, нет в наличии).

    Несёт список названий, которые не попали в корзину, чтобы не формировать
    молча неполный PDF, а показать менеджеру понятное сообщение.
    """

    def __init__(self, missing_titles: list[str]) -> None:
        self.missing_titles = missing_titles
        super().__init__("Не удалось добавить в корзину: " + ", ".join(missing_titles))


def product_slug(url: str) -> str:
    """Извлечь slug товара из URL вида .../products/<slug>/.

    Сначала отбрасываем query/fragment, и только потом снимаем слэши — иначе
    при наличии «?...» у одной ссылки и его отсутствии у другой slug'и одного и
    того же товара разойдутся (foo vs foo/) и состав корзины сверится неверно.
    """
    tail = re.sub(r".*/products/", "", url or "")
    tail = tail.split("?")[0].split("#")[0]
    return tail.strip("/")


def _parse_price(text: str) -> float | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return float(digits) if digits else None


async def _basket_response_ok(resp) -> bool:
    """Проверить, что POST /local/ajax/basket.php подтвердил добавление."""
    if not resp.ok:
        return False
    try:
        data = await resp.json()
        if isinstance(data, dict):
            result = data.get("RESULT", data.get("result"))
            if result is not None:
                return int(result) == 1
    except Exception:  # noqa: BLE001 — не JSON; пробуем по тексту
        pass
    try:
        text = (await resp.text()).strip()
        return '"RESULT":1' in text.replace(" ", "") or '"result":1' in text.replace(" ", "")
    except Exception:  # noqa: BLE001
        return False


def _parse_availability_status(text: str) -> bool | None:
    """Разобрать текст статуса ekrom → in_stock / unknown."""
    low = (text or "").strip().lower()
    if not low:
        return None
    if "нет в наличии" in low:
        return False
    if "под заказ" in low or "в наличии" in low:
        return True
    return None


async def _read_availability(scope) -> bool | None:
    """Прочитать статус наличия из блока карточки или страницы товара."""
    status_el = scope.locator(SEL_PRODUCT_STATUS).first
    if await status_el.count():
        try:
            parsed = _parse_availability_status(await status_el.inner_text())
            if parsed is not None:
                return parsed
        except Exception:  # noqa: BLE001
            pass

    oos_el = scope.locator(SEL_PRODUCT_OUT_OF_STOCK).first
    try:
        if await oos_el.count() and await oos_el.is_visible():
            return False
    except Exception:  # noqa: BLE001
        pass
    return None


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

    async def new_page(self) -> Page:
        """Создать дополнительную вкладку (для параллельной обработки позиций)."""
        if self._context is None:
            raise RuntimeError("EkromClient не запущен — вызовите start()/используйте 'async with'")
        page = await self._context.new_page()
        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))
        return page

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

    async def is_authenticated(self) -> bool:
        """Проверить, жива ли сохранённая сессия (открыт ли личный кабинет).

        Незалогиненного пользователя ekrom редиректит на `/?needAuth=Y`, а в
        шапке показывает ссылку «Войти» вместо «Выйти».
        """
        url = f"{self._settings.ekrom_base_url.rstrip('/')}/personal/"
        await self.page.goto(url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(1200)
        if "needauth" in self.page.url.lower():
            return False
        logout = await self.page.locator("a[href*='logout'], a:has-text('Выйти')").count()
        return logout > 0

    # ── Каталог ────────────────────────────────────────────────────────────────
    async def browse_category(
        self, category_path: str, limit: int = 60, page: Page | None = None
    ) -> list[CatalogProduct]:
        """Прочитать товары со страницы категории (без фильтров)."""
        url = f"{self._settings.ekrom_base_url.rstrip('/')}{category_path}"
        return await self.browse_url(url, limit, page=page)

    async def browse_url(
        self, url: str, limit: int = 60, page: Page | None = None
    ) -> list[CatalogProduct]:
        """Перейти по готовому URL категории (возможно, с фильтрами) и собрать карточки.

        Поиск по сайту (/search/) карточки не отдаёт, поэтому работаем по URL категорий.
        """
        page = page or self.page
        await page.goto(url, wait_until="domcontentloaded")
        await self._wait_for_cards(page)
        return await self._collect_cards(limit, page)

    async def search(
        self, query: str, limit: int = 20, page: Page | None = None
    ) -> list[CatalogProduct]:
        """Поиск по строке сайта (fallback). На ekrom отдаёт мало карточек —
        предпочтительнее browse_category()."""
        page = page or self.page
        url = build_search_url(self._settings.ekrom_base_url, query)
        await page.goto(url, wait_until="domcontentloaded")
        await self._wait_for_cards(page)
        return await self._collect_cards(limit, page)

    async def _wait_for_cards(self, page: Page | None = None) -> None:
        """Дождаться появления карточек товаров вместо фиксированной паузы.

        Списки догружаются скриптом; ждём первую карточку (до 6 c) и даём
        небольшую досборку. Если карточек нет — выходим сразу (категория пуста).
        """
        page = page or self.page
        try:
            await page.wait_for_selector(SEL_PRODUCT_CARD, timeout=6000, state="attached")
        except Exception:  # noqa: BLE001 — пусто/не догрузилось; _collect_cards вернёт []
            return
        await page.wait_for_timeout(300)

    async def _collect_cards(self, limit: int, page: Page | None = None) -> list[CatalogProduct]:
        page = page or self.page
        products: list[CatalogProduct] = []
        cards = page.locator(SEL_PRODUCT_CARD)
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

            in_stock: bool | None = await _read_availability(card)

            if not title or not href:
                continue
            products.append(
                CatalogProduct(
                    title=title,
                    url=self._abs_url(href),
                    price=price,
                    product_id=product_id,
                    in_stock=in_stock,
                )
            )
        return products

    async def is_product_available(
        self, product: CatalogProduct, page: Page | None = None
    ) -> bool:
        """Проверить наличие на странице товара (кэширует результат в product.in_stock)."""
        if product.in_stock is not None:
            return product.in_stock

        page = page or self.page
        await page.goto(product.url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1200)

        parsed = await _read_availability(page.locator(".js-detail").first)
        if parsed is None:
            parsed = await _read_availability(page)
        if parsed is not None:
            product.in_stock = parsed
            return parsed

        btn = page.locator(SEL_ADD_TO_CART).first
        try:
            if not await btn.count() or not await btn.is_visible() or not await btn.is_enabled():
                product.in_stock = False
                return False
        except Exception:  # noqa: BLE001
            product.in_stock = False
            return False

        product.in_stock = True
        return True

    async def read_product_properties(self, page: Page | None = None) -> dict[str, str]:
        """Считать пары «характеристика → значение» с открытой страницы товара.

        Блок характеристик ekrom — список li.product-info__property с парой
        <p class="product-info__property-name">/<p class="..__property-value">.
        """
        page = page or self.page
        names = page.locator(".product-info__property-name")
        values = page.locator(".product-info__property-value")
        try:
            count = min(await names.count(), await values.count())
        except Exception:  # noqa: BLE001
            return {}
        props: dict[str, str] = {}
        for i in range(count):
            try:
                name = (await names.nth(i).inner_text()).strip()
                value = (await values.nth(i).inner_text()).strip()
            except Exception:  # noqa: BLE001
                continue
            if name and value:
                props[name] = value
        return props

    async def ensure_enriched(self, product: CatalogProduct, page: Page | None = None) -> None:
        """Догрузить характеристики товара в product.attributes (если их ещё нет).

        Переиспользует уже открытую страницу товара (после проверки наличия) —
        переходит на неё только если открыто что-то другое. Ошибки чтения не
        критичны: остаёмся без характеристик, подбор тогда опирается на название.
        """
        if product.attributes:
            return
        page = page or self.page
        try:
            if product_slug(page.url) != product_slug(product.url):
                await page.goto(product.url, wait_until="domcontentloaded")
                await page.wait_for_timeout(800)
            props = await self.read_product_properties(page)
            if props:
                product.attributes = props
        except Exception:  # noqa: BLE001 — обогащение не критично для потока
            pass

    # ── Корзина ────────────────────────────────────────────────────────────────
    async def add_to_cart(self, product: CatalogProduct, page: Page | None = None) -> bool:
        """Открыть страницу товара и нажать кнопку «Добавить/В корзину».

        Подтверждаем добавление по ответу POST /local/ajax/basket.php (RESULT=1).
        Товары «под заказ» на ekrom тоже кладутся в корзину — это не «нет в наличии».
        """
        page = page or self.page
        await page.goto(product.url, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(".js-detail", timeout=8000, state="attached")
        except Exception:  # noqa: BLE001 — блок мог уже быть
            pass

        btn = page.locator(SEL_ADD_TO_CART).first
        try:
            await btn.wait_for(state="visible", timeout=8000)
        except Exception:  # noqa: BLE001 — кнопки нет/не появилась
            return False

        def _is_basket(resp) -> bool:
            return "local/ajax/basket.php" in resp.url and resp.request.method == "POST"

        for attempt in range(3):
            try:
                await btn.scroll_into_view_if_needed(timeout=4000)
            except Exception:  # noqa: BLE001
                pass
            force = attempt > 0
            try:
                async with page.expect_response(_is_basket, timeout=15000) as resp_info:
                    await btn.click(timeout=8000, force=force)
                if await _basket_response_ok(await resp_info.value):
                    return True
            except Exception:  # noqa: BLE001 — таймаут/клик не прошёл; ретрай
                pass
            await page.wait_for_timeout(600 + attempt * 400)
        return False

    async def open_cart(self, page: Page | None = None) -> None:
        """Открыть страницу корзины/оформления (тяжёлая страница order/make).

        Используем wait_until='commit' + явное ожидание ключевых элементов,
        чтобы не ждать полной загрузки всех подресурсов страницы.
        """
        page = page or self.page
        cart_url = f"{self._settings.ekrom_base_url.rstrip('/')}/personal/cart/"
        try:
            await page.goto(cart_url, wait_until="commit")
        except Exception:  # noqa: BLE001 — навигация могла уже идти
            pass
        try:
            await page.wait_for_selector(
                "a[href*='/products/'], .basket-delete-selected-btn, "
                f"{SEL_CART_PDF_DOWNLOAD}",
                timeout=4500,
                state="attached",
            )
            # wait_for_selector срабатывает на ПЕРВОМ товаре, тогда как список
            # может дорисовываться. Короткая стабилизация исключает ложное
            # «товара не хватает» и дорогой повторный цикл добавления.
            await page.wait_for_timeout(700)
        except Exception:  # noqa: BLE001 — корзина пуста/не догрузилась
            await page.wait_for_timeout(400)

    async def read_cart_slugs(self, page: Page | None = None) -> set[str]:
        """Прочитать slug'и товаров с УЖЕ открытой страницы корзины."""
        page = page or self.page
        hrefs = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href*=\"/products/\"]'))"
            ".map(a => a.getAttribute('href'))"
        )
        return {product_slug(h) for h in hrefs if h and "/products/" in h}

    async def cart_slugs(self, page: Page | None = None) -> set[str]:
        """Множество slug'ов товаров, фактически лежащих в корзине.

        Состав читаем по ссылкам на товары (a[href*='/products/']) — это
        надёжнее, чем считать строки (.cart-item даёт дубли мобайл/десктоп).
        """
        page = page or self.page
        await self.open_cart(page)
        return await self.read_cart_slugs(page)

    async def clear_cart(self, already_open: bool = False) -> None:
        """Очистить корзину: «Выбрать все» → «Удалить выбранные», в цикле до пустой.

        На странице корзины нет одиночной кнопки «Очистить»; надёжный путь —
        выделить все позиции и удалить выбранные, повторяя при необходимости.
        Пустоту определяем по фактическому наличию товаров (ссылок), а не по
        кнопке удаления — это надёжнее при быстрой навигации. Если страница уже
        открыта (already_open=True), не перезагружаем её на первой итерации.
        """
        for i in range(6):
            if i > 0 or not already_open:
                await self.open_cart()

            if not await self.read_cart_slugs():
                return  # корзина пуста — готово

            select_all = self.page.locator(".basket-select-all-btn").first
            del_btn = self.page.locator(".basket-delete-selected-btn").first
            try:
                if not (await del_btn.count() and await del_btn.is_visible()):
                    return  # товары есть, но удалить нечем — не зацикливаемся
                # Удаление вызывает нативный confirm() (авто-обработчик) и
                # навигацию — клики/ожидания терпимы к этому.
                if await select_all.count():
                    await select_all.click(timeout=4000)
                    await self.page.wait_for_timeout(300)
                await del_btn.click(timeout=5000)
            except Exception:  # noqa: BLE001 — навигация во время клика — это ок
                pass

            await self.page.wait_for_timeout(800)

    async def download_cart_pdf_direct(self, out_path: Path) -> Path:
        """Скачать PDF корзины прямым запросом к cart_pdf.php (без рендера страницы).

        Кнопка «PDF» на сайте просто дёргает этот эндпоинт, который генерит
        документ из серверной корзины. Запрос идёт через контекст браузера, так
        что куки сессии подхватываются автоматически. При любой осечке —
        откатываемся на скачивание через кнопку.
        """
        if self._context is None:
            raise RuntimeError("EkromClient не запущен")
        pdf_url = f"{self._settings.ekrom_base_url.rstrip('/')}/personal/cart_pdf.php"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Генерация PDF на сервере небыстрая — даём щедрый таймаут.
            resp = await self._context.request.get(pdf_url, timeout=45000)
            body = await resp.body()
            if resp.status == 200 and body[:5] == b"%PDF-":
                out_path.write_bytes(body)
                return out_path
        except Exception:  # noqa: BLE001 — упадём в фолбэк ниже
            pass
        return await self.download_cart_pdf(out_path, navigate=True)

    async def download_cart_pdf(self, out_path: Path, navigate: bool = True) -> Path:
        """Скачать PDF со списком товаров из корзины.

        Требует авторизованной сессии (storage_state). Если navigate=False,
        считаем, что страница корзины уже открыта (экономит тяжёлую загрузку).
        """
        if navigate:
            await self.open_cart()

        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Кнопка PDF активна только когда к ней привязан обработчик
        # (атрибут data-bound-pdf='Y'). Ждём именно это состояние, иначе клик
        # «вхолостую» не вызывает скачивания — особенно при быстрой навигации.
        bound = self.page.locator("[data-bound-pdf='Y']").first
        try:
            await bound.wait_for(state="visible", timeout=10000)
            target = bound
        except Exception:  # noqa: BLE001 — атрибута нет; берём любую кнопку печати
            target = self.page.locator(SEL_CART_PDF_DOWNLOAD).first
            await target.wait_for(state="visible", timeout=8000)

        last_err: Exception | None = None
        for attempt in range(2):
            try:
                async with self.page.expect_download(timeout=20000) as dl_info:
                    await target.click(timeout=8000)
                download = await dl_info.value
                await download.save_as(str(out_path))
                return out_path
            except Exception as err:  # noqa: BLE001 — клик не дал download, ретрай
                last_err = err
                await self.page.wait_for_timeout(1500)
        raise last_err if last_err else RuntimeError("PDF download failed")

    # ── Вспомогательное ─────────────────────────────────────────────────────────
    def _abs_url(self, href: str) -> str:
        if href.startswith("http"):
            return href
        return f"{self._settings.ekrom_base_url.rstrip('/')}/{href.lstrip('/')}"
