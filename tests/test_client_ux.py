"""Тесты клиентских текстов и UX-хелперов."""

from __future__ import annotations

from max_ekrom_bot.config import Settings
from max_ekrom_bot.messenger.client_ux import (
    EXAMPLE_QUERIES,
    format_client_selection,
    is_smalltalk,
    smalltalk_reply,
    welcome_text,
)
from max_ekrom_bot.models import CatalogProduct, MatchedItem, ProductSpec, SelectionResult


def test_smalltalk_detected():
    assert is_smalltalk("привет")
    assert is_smalltalk("Добрый день")
    assert is_smalltalk("  ")
    assert not is_smalltalk("духовой шкаф чёрный")


def test_welcome_has_shop_and_link():
    s = Settings(SHOP_NAME="ekrom", SHOP_SITE="https://www.ekrom.ru")
    text = welcome_text(s)
    assert "ekrom" in text
    assert "ekrom.ru" in text


def test_format_client_selection_header():
    p = CatalogProduct(title="Духовка", url="u", price=1000)
    sel = SelectionResult(items=[MatchedItem(spec=ProductSpec(), product=p)])
    out = format_client_selection(sel, "ekrom")
    assert "Ваш подбор готов" in out
    assert "1 000 ₽" in out
    assert "менеджер" in out.lower()


def test_example_queries_non_empty():
    assert len(EXAMPLE_QUERIES) >= 3


def test_smalltalk_reply_friendly():
    assert "💡" in smalltalk_reply("ekrom") or "Примеры" in smalltalk_reply("ekrom")
