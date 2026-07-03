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


def test_contacts_text_includes_showrooms_and_email():
    from max_ekrom_bot.messenger.client_ux import contacts_text

    s = Settings(
        LLM_PROVIDER="rule",
        SHOP_CONTACTS="+7 (343) 379-98-68, ежедневно 9:00-18:00",
        SHOP_SITE="https://www.ekrom.ru",
    )
    out = contacts_text(s)
    assert "sale@ekrom.ru" in out
    assert "Толмачева, 22" in out
    assert "Цвиллинга, 1" in out
    assert "Верх-Исетский" in out
    assert "Тюмень" in out and "Герцена, 55" in out


def test_followup_keyboard_has_whatsapp_and_no_tel():
    from max_ekrom_bot.messenger.client_ux import followup_keyboard

    s = Settings(
        LLM_PROVIDER="rule",
        SHOP_CONTACTS="+7 (343) 379-98-68",
        SHOP_SITE="https://www.ekrom.ru",
    )
    kb = followup_keyboard(s)
    urls = [b.url for row in kb.inline_keyboard for b in row if b.url]
    assert any(u.startswith("https://wa.me/7343") for u in urls)
    # tel:-URL Telegram отвергает (Bad Request на всё сообщение) — их быть не должно
    assert not any(u.startswith("tel:") for u in urls)


def test_followup_keyboard_without_phone_skips_contact_row():
    from max_ekrom_bot.messenger.client_ux import followup_keyboard

    s = Settings(LLM_PROVIDER="rule", SHOP_CONTACTS="", SHOP_SITE="https://x.ru")
    kb = followup_keyboard(s)
    urls = [b.url for row in kb.inline_keyboard for b in row if b.url]
    assert not any("wa.me" in u for u in urls)
