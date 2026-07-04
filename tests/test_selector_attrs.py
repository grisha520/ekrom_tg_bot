"""Тесты подбора по расширенным характеристикам: объём, загрузка, класс, материал,
и универсальное сопоставление spec.extra с атрибутами товара."""

from __future__ import annotations

from max_ekrom_bot.matching.selector import (
    ProductSelector,
    _canon_energy,
    _canon_material,
    _find_attr_value,
    _value_matches,
)
from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec


def _p(title, price, attrs):
    return CatalogProduct(title=title, url=f"u/{title}", price=price, attributes=attrs)


def test_canon_material_and_energy():
    assert _canon_material("Искусственный камень") == "камень"
    assert _canon_material("Нержавеющая сталь") == "нержавейка"
    assert _canon_energy("A+ / F") == "A+"
    assert _canon_energy("А++") == "A++"  # кириллица


def test_material_hard_filter():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.UNKNOWN, material="камень")
    stone = _p("Мойка гранит", 100, {"Материал": "Искусственный камень"})
    steel = _p("Мойка сталь", 90, {"Материал": "Нержавеющая сталь"})
    ranked = sel.ranked_candidates(spec, [stone, steel])
    assert [c.title for c in ranked] == ["Мойка гранит"]


def test_volume_ranks_closest():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.FRIDGE, volume_l=300)
    a = _p("Холод A", 100, {"Общий/полезный объем, л": "180"})
    b = _p("Холод B", 100, {"Общий/полезный объем, л": "310"})
    ranked = sel.ranked_candidates(spec, [a, b])
    assert ranked[0].title == "Холод B"


def test_load_ranks_closest():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.UNKNOWN, load_kg=8)
    a = _p("Стир A", 100, {"Max загрузка белья, кг": "6"})
    b = _p("Стир B", 100, {"Max загрузка белья, кг": "8"})
    ranked = sel.ranked_candidates(spec, [a, b])
    assert ranked[0].title == "Стир B"


def test_energy_class_equal_or_better():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.UNKNOWN, energy_class="A+")
    good = _p("A+++", 100, {"Класс энергопотребления": "A+++"})
    bad = _p("C", 100, {"Класс энергопотребления": "C"})
    ranked = sel.ranked_candidates(spec, [bad, good])
    assert ranked[0].title == "A+++"


def test_extra_generic_attribute_matching():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.UNKNOWN, extra={"отжим": "1200"})
    a = _p("Стир A", 100, {"Скорость отжима, оборотов/мин": "800"})
    b = _p("Стир B", 100, {"Скорость отжима, оборотов/мин": "1200"})
    ranked = sel.ranked_candidates(spec, [a, b])
    assert ranked[0].title == "Стир B"


def test_find_attr_value_fuzzy_key():
    p = _p("X", 1, {"Количество камер": "2", "Уровень шума, дБ": "42"})
    assert _find_attr_value(p, "камер") == "2"
    assert _find_attr_value(p, "уровень шума") == "42"
    assert _find_attr_value(p, "несуществующее") is None


def test_value_matches_numeric_tolerance_and_text():
    assert _value_matches("1200", "1200 об/мин") is True
    assert _value_matches("300", "310") is True   # ±15%
    assert _value_matches("300", "180") is False
    assert _value_matches("сенсорное", "Сенсорное управление") is True
