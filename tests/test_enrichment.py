"""Тесты атрибут-зависимых детекторов характеристик (#4).

Проверяют чтение сигналов из структурированных характеристик товара, а не
только из названия.
"""

from __future__ import annotations

from max_ekrom_bot.matching.selector import (
    ProductSelector,
    _product_burners,
    _product_colors,
    _product_fuels,
    _product_width,
)
from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec


def _p(title: str, attrs: dict[str, str] | None = None, price: float = 1.0) -> CatalogProduct:
    return CatalogProduct(title=title, url="u", price=price, attributes=attrs or {})


def test_color_from_attribute():
    assert "чёрный" in _product_colors(_p("Духовка LEX EDM", {"Цвет": "Чёрный"}))


def test_fuel_from_type_attr_ignores_management_field():
    # «Тип управления: Электронное» НЕ должно дать электрическую плиту,
    # а «Вид варочной поверхности: Газовая» — должно дать газовую.
    p = _p("Панель", {"Тип управления": "Электронное", "Вид варочной поверхности": "Газовая"})
    assert _product_fuels(p) == {"газовая"}


def test_burners_from_attribute():
    assert _product_burners(_p("Панель LEX", {"Количество конфорок": "4"})) == 4


def test_width_from_embedding_attribute():
    assert _product_width(_p("Духовка", {"Ширина встраивания, мм": "600"})) == 600


def test_helpers_fallback_to_title_without_attrs():
    assert _product_burners(_p("Панель на 3 конфорки")) == 3
    assert "белый" in _product_colors(_p("Духовка белая"))


def test_attribute_contradiction_dropped():
    # Цвет не виден в названии, но виден в характеристиках — противоречие ловится.
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.OVEN, color="белый")
    black = _p("Духовка LEX EDM", {"Цвет": "Чёрный"}, price=100)
    white = _p("Духовка BOSCH HBF", {"Цвет": "Белый"}, price=120)
    ranked = sel.ranked_candidates(spec, [black, white])
    assert [c.title for c in ranked] == ["Духовка BOSCH HBF"]


def test_width_tolerance_595_matches_600():
    # Корпусная ширина 595 мм должна засчитываться под запрос «600».
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.OVEN, width_mm=600)
    a = _p("Духовка A", {"Ширина, мм": "595"}, price=200)
    b = _p("Духовка B", {}, price=200)
    ranked = sel.ranked_candidates(spec, [b, a])  # b раньше по входу
    assert ranked[0].title == "Духовка A"  # совпадение ширины поднимает A выше
