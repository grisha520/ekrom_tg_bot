"""Тесты своего генератора клиентского PDF-подбора."""

from __future__ import annotations

import pytest

pytest.importorskip("reportlab")  # модуль опционален в окружениях без reportlab

from pypdf import PdfReader  # noqa: E402

from max_ekrom_bot.config import Settings  # noqa: E402
from max_ekrom_bot.models import (  # noqa: E402
    ApplianceType,
    CatalogProduct,
    MatchedItem,
    ProductSpec,
    SelectionResult,
)
from max_ekrom_bot.pdf.offer import _attrs_line, build_offer_pdf  # noqa: E402


def _selection() -> SelectionResult:
    return SelectionResult(
        items=[
            MatchedItem(
                spec=ProductSpec(appliance=ApplianceType.OVEN),
                product=CatalogProduct(
                    title="Электрический духовой шкаф Lex EDM 4575 BBL",
                    url="https://www.ekrom.ru/products/lex_edm_4575/",
                    price=29990.0,
                    attributes={
                        "Цвет": "Чёрный",
                        "Тип установки": "Встраиваемый",
                        "Артикул": "EDM4575",  # должен скрыться
                    },
                ),
                reason="x",
            ),
            MatchedItem(
                spec=ProductSpec(appliance=ApplianceType.HOOD),
                product=CatalogProduct(
                    title="Кухонная вытяжка Lex HUBBLE 600 INOX",
                    url="https://www.ekrom.ru/products/lex_hubble_600/",
                    price=7790.0,
                    attributes={"Цвет": "Нержавейка"},
                ),
                reason="y",
            ),
        ]
    )


def test_build_offer_pdf_creates_readable_file(tmp_path):
    out = tmp_path / "offer.pdf"
    result = build_offer_pdf(_selection(), Settings(LLM_PROVIDER="rule"), out)
    assert result.exists()
    assert result.stat().st_size > 800

    text = "".join(page.extract_text() or "" for page in PdfReader(str(out)).pages)
    # латиница извлекается надёжно (кириллица в TTF тоже, но не зависим от движка)
    assert "Lex EDM 4575" in text
    assert "Lex HUBBLE 600" in text


def test_attrs_line_hides_service_fields_and_limits():
    line = _attrs_line(
        {
            "Цвет": "Белый",
            "Артикул": "ABC",  # скрыт
            "Производитель": "Lex",  # скрыт
            "Ширина, мм": "595",
        }
    )
    assert "Цвет: Белый" in line
    assert "Ширина, мм: 595" in line
    assert "Артикул" not in line
    assert "Производитель" not in line


def test_total_price_in_selection():
    assert _selection().total_price == pytest.approx(37780.0)
