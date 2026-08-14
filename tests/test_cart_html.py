"""Тесты HTML-вёрстки PDF-комплекта «точь-в-точь» (рендер не требует браузера)."""

from __future__ import annotations

from max_ekrom_bot.config import Settings
from max_ekrom_bot.matching.combo_pricing import ComboResult
from max_ekrom_bot.models import CatalogProduct, MatchedItem, ProductSpec, SelectionResult
from max_ekrom_bot.pdf.cart_html import build_cart_html

_S = Settings(LLM_PROVIDER="rule", MANAGER_NAME="Григорий Алексеев",
              MANAGER_PHONE="+7 (950) 197 17 90")


def _sel():
    return SelectionResult(items=[
        MatchedItem(spec=ProductSpec(), product=CatalogProduct(
            title="Духовой шкаф Kuppersberg HO 608", url="u", price=45514,
            attributes={"Артикул": "9069", "Цвет": "Чёрный", "Страна производитель": "Турция",
                        "Гарантия, мес": "24"})),
        MatchedItem(spec=ProductSpec(), product=CatalogProduct(
            title="Встраиваемый холодильник Kuppersberg RBN 1965", url="u2", price=40000,
            old_price=97281, discount_pct=58.88,
            attributes={"Артикул": "8806", "Страна производитель": "Китай"})),
    ])


def test_html_has_core_blocks():
    combo = ComboResult(mechanic="spec_price", subtotal=85514, discount_total=57281,
                        final_total=28233, applied=True)
    html = build_cart_html(_sel(), _S, combo)
    # шапка + шрифт Rubik + логотип
    assert "Rubik" in html and "<svg" in html and _S.pdf_header_phone in html
    # товары (заголовки uppercase)
    assert "ДУХОВОЙ ШКАФ KUPPERSBERG HO 608" in html
    # блок итого в формате эталона
    assert "Итого" in html and "Индивидуальная скидка" in html
    assert "Сумма со скидкой" in html
    # менеджер + шоурумы
    assert "Григорий Алексеев" in html and "Толмачева" in html


def test_html_per_item_discount_badge():
    combo = ComboResult(mechanic="spec_price", subtotal=85514, discount_total=57281,
                        final_total=28233, applied=True)
    html = build_cart_html(_sel(), _S, combo)
    # у холодильника — «Скидка -58.88%» и зачёркнутая старая цена
    assert "Скидка -58.88%" in html
    assert "97 281 ₽" in html          # old_price
    assert "line-through" in html      # зачёркивание в стиле


def test_html_individual_discount_percent():
    combo = ComboResult(mechanic="flat_combo", subtotal=100000, discount_total=11000,
                        final_total=89000, applied=True, combo_discount_pct=11)
    html = build_cart_html(_sel(), _S, combo)
    assert "Индивидуальная скидка" in html and "-11.00%" in html


def test_html_escapes_titles():
    sel = SelectionResult(items=[MatchedItem(spec=ProductSpec(), product=CatalogProduct(
        title="Плита <b>A&B</b>", url="u", price=1000))])
    html = build_cart_html(sel, _S, None)
    assert "&lt;B&gt;A&amp;B" in html   # HTML-инъекция экранирована (заголовок uppercase)


def test_html_gift_line_free():
    combo = ComboResult(mechanic="gift", subtotal=60000, discount_total=5990,
                        final_total=60000, applied=True, gift_title="Чайник Korting KWK 0902")
    html = build_cart_html(_sel(), _S, combo)
    assert "В подарок" in html and "бесплатно" in html and "KWK 0902" in html


def test_html_without_combo_renders_price_list():
    html = build_cart_html(_sel(), _S, None)
    assert "Итого" in html and "Сумма со скидкой" in html


def test_html_country_flags_rendered():
    # У товаров страны Турция/Китай → круглые SVG-флаги (clip-path), а не эмодзи.
    html = build_cart_html(_sel(), _S, None)
    assert "clip-path" in html


def test_html_zero_discount_shows_row():
    # «Индивидуальная скидка 0 ₽» показывается всегда (как в оригинале корзины).
    combo = ComboResult(mechanic="none", subtotal=85514, discount_total=0, final_total=85514)
    html = build_cart_html(_sel(), _S, combo)
    assert "Индивидуальная скидка" in html


# ── Сверка с эталоном сайта (document (1).pdf от 14.08.2026) ──────────────────

def test_subtitle_says_korzina_like_reference():
    # В документе сайта шапка — «Корзина от <дата> <время> (г. …)», не «Комплект».
    from max_ekrom_bot.config import Settings
    from max_ekrom_bot.models import (
        ApplianceType, CatalogProduct, MatchedItem, ProductSpec, SelectionResult,
    )

    item = MatchedItem(
        spec=ProductSpec(appliance=ApplianceType.OVEN, raw_text="духовка"),
        product=CatalogProduct(title="Духовой шкаф", url="u", price=1000),
        candidates_count=1,
    )
    html = build_cart_html(SelectionResult(items=[item]), Settings(telegram_bot_token="x"))
    assert "Корзина от" in html
    assert "Комплект от" not in html


def test_warranty_word_form_matches_reference():
    # Эталон: «Срок гарантии 24 месяца» (не «24 месяцев»).
    from max_ekrom_bot.pdf.cart_html import _warranty_months

    assert _warranty_months("24") == "24 месяца"
    assert _warranty_months("12 мес") == "12 месяцев"
    assert _warranty_months("1") == "1 месяц"
    assert _warranty_months("36") == "36 месяцев"
    assert _warranty_months("") == "-"
    assert _warranty_months("-") == "-"


def test_country_row_kept_in_characteristics():
    # В эталоне «Страна производитель Китай» присутствует В характеристиках.
    from max_ekrom_bot.config import Settings
    from max_ekrom_bot.models import (
        ApplianceType, CatalogProduct, MatchedItem, ProductSpec, SelectionResult,
    )

    product = CatalogProduct(
        title="Газовая варочная панель Midea MG449TGB черная", url="u", price=14990,
        attributes={"Артикул": "MG449TGB", "Страна производитель": "Китай",
                    "Гарантия, мес": "24", "Промо": "служебное"},
    )
    item = MatchedItem(
        spec=ProductSpec(appliance=ApplianceType.COOKTOP, raw_text="варочная"),
        product=product, candidates_count=1,
    )
    html = build_cart_html(SelectionResult(items=[item]), Settings(telegram_bot_token="x"))
    assert "Страна производитель" in html
    assert "Промо" not in html            # служебные поля по-прежнему скрыты
    assert "24 месяца" in html


def test_download_links_point_to_product():
    # «Инструкция/Схема» без href — мёртвые ссылки; ведём на карточку товара.
    from max_ekrom_bot.config import Settings
    from max_ekrom_bot.models import (
        ApplianceType, CatalogProduct, MatchedItem, ProductSpec, SelectionResult,
    )

    item = MatchedItem(
        spec=ProductSpec(appliance=ApplianceType.OVEN, raw_text="духовка"),
        product=CatalogProduct(title="Духовой шкаф",
                               url="https://www.ekrom.ru/products/oven/", price=1000),
        candidates_count=1,
    )
    html = build_cart_html(SelectionResult(items=[item]), Settings(telegram_bot_token="x"))
    assert 'href="https://www.ekrom.ru/products/oven/">Инструкция' in html


def test_pdf_layer_does_not_import_messenger():
    # Регресс: генераторы PDF брали шоурумы из client_ux (тянет aiogram, который
    # при импорте подменяет политику asyncio на uvloop) — Chromium падал, и PDF
    # молча уходил в reportlab-фолбэк. Слой печати не должен знать о мессенджере.
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "max_ekrom_bot" / "pdf"
    for path in src.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "messenger" not in text, f"{path.name} импортирует слой мессенджера"


def test_characteristics_follow_reference_order():
    # Эталон Midea MG449TGB: артикул → цвет → модель → страна → гарантия →
    # высота → вес → специфичные (конфорки…). Габариты, кроме высоты, сайт не
    # печатает вовсе — иначе карточка забивается «ширина/глубина/встраивание».
    import re

    from max_ekrom_bot.config import Settings
    from max_ekrom_bot.models import (
        ApplianceType, CatalogProduct, MatchedItem, ProductSpec, SelectionResult,
    )

    product = CatalogProduct(
        title="Газовая варочная панель Midea MG449TGB черная", url="u", price=14990,
        attributes={
            "Ширина, мм": "450", "Глубина, мм": "510", "Высота, мм": "102",
            "Ширина встраивания, мм": "410", "Глубина встраивания, мм": "480",
            "Цвет": "Черный", "Количество конфорок": "3 конфорки",
            "Артикул": "MG449TGB", "Модель": "MG449TGB",
            "Страна производитель": "Китай", "Гарантия, мес": "24",
            "Вес, кг": "11.52", "Электроподжиг": "Да",
        },
    )
    item = MatchedItem(
        spec=ProductSpec(appliance=ApplianceType.COOKTOP, raw_text="варочная"),
        product=product, candidates_count=1,
    )
    html = build_cart_html(SelectionResult(items=[item]), Settings(telegram_bot_token="x"))
    keys = re.findall(r'<td class="k">([^<]+)</td>', html)

    assert keys[:7] == ["Артикул", "Цвет", "Модель", "Страна производитель",
                        "Гарантия, мес", "Высота, мм", "Вес, кг"]
    assert "Количество конфорок" in keys       # специфичные — следом
    # Габариты, кроме высоты, в документ не попадают.
    assert not any(k.startswith(("Ширина", "Глубина")) for k in keys)
    assert not any("встраивания" in k for k in keys)


def test_showrooms_limited_to_document_city():
    # Документ формируется для конкретного города («Корзина от … (г. N)»).
    # Печатать шоурумы другого города — обещать то, чего у клиента нет.
    from max_ekrom_bot.config import Settings
    from max_ekrom_bot.models import (
        ApplianceType, CatalogProduct, MatchedItem, ProductSpec, SelectionResult,
    )

    item = MatchedItem(
        spec=ProductSpec(appliance=ApplianceType.OVEN, raw_text="духовка"),
        product=CatalogProduct(title="Духовой шкаф", url="u", price=1000),
        candidates_count=1,
    )
    sel = SelectionResult(items=[item])

    # Settings принимает ТОЛЬКО алиасы (PDF_CITY): форму с именем поля
    # pydantic молча игнорирует из-за extra="ignore".
    ekb = build_cart_html(sel, Settings(telegram_bot_token="x", PDF_CITY="Екатеринбург"))
    assert "Толмачева" in ekb
    assert "Герцена" not in ekb            # тюменский адрес не попадает
    assert "в 3-х шоурумах" in ekb          # число согласовано со списком

    tmn = build_cart_html(sel, Settings(telegram_bot_token="x", PDF_CITY="Тюмень"))
    assert "Герцена" in tmn and "Толмачева" not in tmn
    assert "в 1-х шоурумах" in tmn


def test_cards_do_not_split_across_pages():
    # В документе сайта товар всегда целиком на одной странице.
    from max_ekrom_bot.config import Settings
    from max_ekrom_bot.models import (
        ApplianceType, CatalogProduct, MatchedItem, ProductSpec, SelectionResult,
    )

    item = MatchedItem(
        spec=ProductSpec(appliance=ApplianceType.OVEN, raw_text="духовка"),
        product=CatalogProduct(title="Духовой шкаф", url="u", price=1000),
        candidates_count=1,
    )
    html = build_cart_html(SelectionResult(items=[item]), Settings(telegram_bot_token="x"))
    assert "break-inside:avoid" in html
