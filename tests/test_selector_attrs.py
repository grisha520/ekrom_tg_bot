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


# ── Несколько значений одного атрибута = «или» ───────────────────────────────

def test_same_attribute_constraints_are_or_not_and():
    # «вытяжка отдельная ИЛИ скошенная ИЛИ труба» → три eq на «Тип кухонной
    # вытяжки». Требовать их одновременно нельзя: не проходил никто, включалось
    # смягчение, и клиент получал полновстраиваемую — ровно то, чего не хотел.
    from max_ekrom_bot.matching.selector import ProductSelector
    from max_ekrom_bot.models import AttrConstraint, CatalogProduct, ProductSpec

    spec = ProductSpec(raw_text="вытяжка отдельная или скошенная или труба",
                       attr_constraints=[
                           AttrConstraint(attribute="Тип кухонной вытяжки", op="eq",
                                          value="Островная"),
                           AttrConstraint(attribute="Тип кухонной вытяжки", op="eq",
                                          value="Наклонная"),
                           AttrConstraint(attribute="Тип кухонной вытяжки", op="eq",
                                          value="Настенная-тубус"),
                       ])
    sel = ProductSelector()

    naklon = CatalogProduct(title="Вытяжка A", url="a", price=10000,
                            attributes={"Тип кухонной вытяжки": "Наклонная"})
    tubus = CatalogProduct(title="Вытяжка B", url="b", price=11000,
                           attributes={"Тип кухонной вытяжки": "Настенная-тубус"})
    builtin = CatalogProduct(title="Вытяжка C", url="c", price=9000,
                             attributes={"Тип кухонной вытяжки": "Полновстраиваемая"})

    assert sel.passes_filters(spec, naklon) is True     # подходит одно из значений
    assert sel.passes_filters(spec, tubus) is True
    assert sel.passes_filters(spec, builtin) is False   # не подходит ни одно

    # И в выдаче полновстраиваемой быть не должно — смягчение не включается.
    ranked = sel.ranked_candidates(spec, [naklon, tubus, builtin])
    assert builtin not in ranked and len(ranked) == 2


def test_different_attributes_still_and():
    # Разные атрибуты по-прежнему требуются вместе.
    from max_ekrom_bot.matching.selector import ProductSelector
    from max_ekrom_bot.models import AttrConstraint, CatalogProduct, ProductSpec

    spec = ProductSpec(raw_text="газовая варочная 600", attr_constraints=[
        AttrConstraint(attribute="Вид варочной поверхности", op="eq", value="Газовая"),
        AttrConstraint(attribute="Тип установки", op="eq", value="Встраиваемый"),
    ])
    sel = ProductSelector()
    good = CatalogProduct(title="Панель A", url="a", price=20000, attributes={
        "Вид варочной поверхности": "Газовая", "Тип установки": "Встраиваемый"})
    wrong = CatalogProduct(title="Панель B", url="b", price=20000, attributes={
        "Вид варочной поверхности": "Газовая", "Тип установки": "Отдельностоящий"})
    assert sel.passes_filters(spec, good) is True
    assert sel.passes_filters(spec, wrong) is False


def test_unknown_attribute_value_not_filtered_out():
    # «Молчание» карточки не должно отсекать товар (у необогащённых атрибута нет).
    from max_ekrom_bot.matching.selector import ProductSelector
    from max_ekrom_bot.models import AttrConstraint, CatalogProduct, ProductSpec

    spec = ProductSpec(raw_text="вытяжка наклонная", attr_constraints=[
        AttrConstraint(attribute="Тип кухонной вытяжки", op="eq", value="Наклонная")])
    silent = CatalogProduct(title="Вытяжка X", url="x", price=9000, attributes={})
    assert ProductSelector().passes_filters(spec, silent) is True
