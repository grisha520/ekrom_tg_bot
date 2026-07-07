"""Тесты PDF-комплекта в стиле корзины ekrom."""

from __future__ import annotations

import pytest

pytest.importorskip("reportlab")

from pypdf import PdfReader  # noqa: E402

from max_ekrom_bot.config import Settings  # noqa: E402
from max_ekrom_bot.models import (  # noqa: E402
    CatalogProduct, MatchedItem, ProductSpec, SelectionResult,
)
from max_ekrom_bot.pdf.cart_style import build_cart_style_pdf  # noqa: E402


def _sel() -> SelectionResult:
    return SelectionResult(items=[
        MatchedItem(spec=ProductSpec(), product=CatalogProduct(
            title="Духовой шкаф Maunfeld EOEC516PB2", url="u", price=34330,
            attributes={"Артикул": "КА-1", "Цвет": "Чёрный", "Гарантия, мес": "36",
                        "Страна производитель": "Китай"})),
        MatchedItem(spec=ProductSpec(), product=CatalogProduct(
            title="Холодильник Lex LCD505", url="u2", price=90573, old_price=129390,
            attributes={"Артикул": "CH-2", "Цвет": "Серый", "Гарантия, мес": "36",
                        "Страна производитель": "Китай"})),
    ])


def test_cart_style_pdf_renders(tmp_path):
    out = tmp_path / "k.pdf"
    build_cart_style_pdf(_sel(), Settings(LLM_PROVIDER="rule", MANAGER_NAME="Иван"),
                         out, combo_discount_pct=20.0)
    assert out.exists() and out.stat().st_size > 1000
    text = "".join(p.extract_text() or "" for p in PdfReader(str(out)).pages)
    assert "EOEC516PB2" in text
    assert "Итого" in text
    assert "Скидка на комплект" in text
    assert "Скидка -30%" in text  # позиционная скидка холодильника (129390→90573)


def _text(out) -> str:
    return "".join(p.extract_text() or "" for p in PdfReader(str(out)).pages)


def test_cart_style_pdf_flat_combo_result(tmp_path):
    from max_ekrom_bot.matching.combo_pricing import ComboResult

    out = tmp_path / "flat.pdf"
    combo = ComboResult(mechanic="flat_combo", subtotal=124903, discount_total=9992.24,
                        final_total=114910.76, applied=True, combo_discount_pct=8,
                        note="Скидка −8% на комплект из 3 позиций.")
    build_cart_style_pdf(_sel(), Settings(LLM_PROVIDER="rule"), out, combo=combo)
    text = _text(out)
    assert "Скидка на комплект" in text and "8%" in text


def test_cart_style_pdf_cascade_result(tmp_path):
    # Каскад: скидка в рублях на наименьший товар, без единого % на комплект.
    from max_ekrom_bot.matching.combo_pricing import ComboResult

    out = tmp_path / "cascade.pdf"
    combo = ComboResult(mechanic="cascade_cheapest", subtotal=124903,
                        discount_total=8582.5, final_total=116320.5, applied=True,
                        item_discounts={0: 25},
                        note="Скидка −25% на самый недорогой товар в комплекте.")
    build_cart_style_pdf(_sel(), Settings(LLM_PROVIDER="rule"), out, combo=combo)
    text = _text(out)
    assert "Скидка на комплект по акции" in text
    assert "наименьший" not in text.lower() or "недорог" in text.lower()


def test_cart_style_pdf_product_without_attributes(tmp_path):
    # Товар без характеристик не должен ронять PDF (reportlab падает на Table([])).
    out = tmp_path / "bare.pdf"
    sel = SelectionResult(items=[
        MatchedItem(spec=ProductSpec(), product=CatalogProduct(
            title="Духовой шкаф Korting OKB 2820 EN", url="u", price=47990)),
    ])
    build_cart_style_pdf(sel, Settings(LLM_PROVIDER="rule"), out)
    assert out.exists() and out.stat().st_size > 1000
    assert "OKB 2820 EN" in _text(out)


def test_cart_style_pdf_manager_review(tmp_path):
    # Подарочная/нестандартная акция: скидки нет, есть пометка про менеджера.
    from max_ekrom_bot.matching.combo_pricing import ComboResult

    out = tmp_path / "gift.pdf"
    combo = ComboResult(mechanic="gift", subtotal=124903, discount_total=0.0,
                        final_total=124903, manager_review=True,
                        note="Точный итог по этой акции уточнит менеджер.")
    build_cart_style_pdf(_sel(), Settings(LLM_PROVIDER="rule"), out, combo=combo)
    text = _text(out)
    assert "менеджер" in text.lower()


def test_cart_style_pdf_gift_line(tmp_path):
    # Оцифрованный подарок: отдельная строка, сумма к оплате НЕ уменьшается.
    from max_ekrom_bot.matching.combo_pricing import ComboResult

    out = tmp_path / "giftline.pdf"
    combo = ComboResult(mechanic="gift", subtotal=124903, discount_total=5990,
                        final_total=124903, applied=True,
                        gift_title="Чайник Korting KWK 0902 G",
                        note="В подарок: Чайник Korting KWK 0902 G — бесплатно.")
    build_cart_style_pdf(_sel(), Settings(LLM_PROVIDER="rule"), out, combo=combo)
    text = _text(out)
    assert "В подарок" in text and "KWK 0902 G" in text
    assert "Сумма к оплате" in text
