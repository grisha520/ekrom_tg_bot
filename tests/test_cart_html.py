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
