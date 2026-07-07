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
