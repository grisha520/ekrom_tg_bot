"""Тесты форматирования превью подбора для Telegram."""

from __future__ import annotations

from max_ekrom_bot.messenger.telegram_format import _fmt_price, format_selection
from max_ekrom_bot.models import (
    CatalogProduct,
    MatchedItem,
    ProductSpec,
    SelectionResult,
)


def test_fmt_price_thousands_separator():
    assert _fmt_price(45990) == "45 990 ₽"
    assert _fmt_price(None) == "—"
    assert _fmt_price(0) == "—"


def test_empty_selection_message():
    assert "Не удалось подобрать" in format_selection(SelectionResult(items=[]))


def test_matched_and_total_rendered():
    p = CatalogProduct(title="Духовка <X>", url="u", price=1000)
    sel = SelectionResult(items=[MatchedItem(spec=ProductSpec(), product=p)])
    out = format_selection(sel)
    assert "Духовка &lt;X&gt;" in out  # HTML-экранирование
    assert "Итого" in out
    assert "1 000 ₽" in out


def test_title_is_clickable_link():
    p = CatalogProduct(
        title="Духовка X",
        url="https://www.ekrom.ru/products/dukhovka_x/?a=1&b=2",
        price=1000,
    )
    out = format_selection(SelectionResult(items=[MatchedItem(spec=ProductSpec(), product=p)]))
    assert '<a href="https://www.ekrom.ru/products/dukhovka_x/?a=1&amp;b=2">Духовка X</a>' in out


def test_substitution_marked():
    p = CatalogProduct(title="Замена", url="u", price=500)
    item = MatchedItem(spec=ProductSpec(), product=p, reason="Предложена замена. ...")
    out = format_selection(SelectionResult(items=[item]))
    assert "замена" in out.lower()


def test_unmatched_listed():
    item = MatchedItem(spec=ProductSpec(raw_text="Тостер"), product=None, reason="нет")
    out = format_selection(SelectionResult(items=[item]))
    assert "Не найдено" in out
    assert "Тостер" in out
