"""Тесты детерминированного подбора (ProductSelector)."""

from __future__ import annotations

from max_ekrom_bot.matching.selector import (
    ProductSelector,
    _canon_color,
    _canon_fuel,
    _title_has_color,
)
from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec


def _p(title: str, price: float | None) -> CatalogProduct:
    return CatalogProduct(title=title, url=f"https://x/products/{price}/", price=price)


def test_color_token_russian_substring():
    assert _title_has_color("Духовой шкаф чёрный", "чёрный")
    assert _title_has_color("Панель белая глянец", "белый")


def test_color_token_english_code_as_whole_token():
    # Код цвета как отдельный токен ловится, часть артикула — нет.
    assert _title_has_color("Korting OGG 771 BL", "чёрный")
    assert not _title_has_color("Korting OGGBL771", "чёрный")


def test_single_letter_codes_are_ignored():
    # Однобуквенные коды (b/w/x) слишком шумные — не должны срабатывать.
    assert not _title_has_color("Bosch HBF 011 B", "чёрный")


def test_middle_segment_picks_median_priced():
    sel = ProductSelector(segment="средний")
    cands = [_p("A", 100), _p("B", 200), _p("C", 300)]
    ranked = sel.ranked_candidates(ProductSpec(), cands)
    assert ranked[0].price == 200


def test_economy_segment_cheapest_first():
    sel = ProductSelector(segment="эконом")
    cands = [_p("A", 300), _p("B", 100), _p("C", 200)]
    ranked = sel.ranked_candidates(ProductSpec(), cands)
    assert [c.price for c in ranked] == [100, 200, 300]


def test_premium_segment_most_expensive_first():
    sel = ProductSelector(segment="премиум")
    cands = [_p("A", 100), _p("B", 300), _p("C", 200)]
    ranked = sel.ranked_candidates(ProductSpec(), cands)
    assert [c.price for c in ranked] == [300, 200, 100]


def test_excluded_color_filtered_out():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.OVEN, exclude_colors=["чёрный"])
    cands = [_p("Шкаф чёрный", 100), _p("Шкаф белый", 120)]
    ranked = sel.ranked_candidates(spec, cands)
    assert [c.title for c in ranked] == ["Шкаф белый"]


def test_excluded_fuel_filtered_out():
    # «кроме газовых» → газовые отсекаются, остаётся электрическая.
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.COOKTOP, exclude_fuel=["газовая"])
    cands = [_p("Панель газовая", 100), _p("Панель электрическая", 120)]
    ranked = sel.ranked_candidates(spec, cands)
    assert [c.title for c in ranked] == ["Панель электрическая"]


def test_excluded_fuel_declension():
    # Исключение в мужском роде («не газовый») нормализуется по стему.
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.COOKTOP, exclude_fuel=["газовый"])
    ranked = sel.ranked_candidates(spec, [_p("Панель газовая", 100), _p("Панель индукционная", 120)])
    assert [c.title for c in ranked] == ["Панель индукционная"]


def test_max_price_caps_selection():
    # Бюджет 30000: дорогие отбрасываются, «средний» считается в пределах бюджета.
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.OVEN, max_price=30000)
    cands = [_p("A", 20000), _p("B", 28000), _p("C", 90000), _p("D", 150000)]
    ranked = sel.ranked_candidates(spec, cands)
    assert all(c.price <= 30000 for c in ranked)
    assert {c.price for c in ranked} == {20000, 28000}


def test_max_price_fallback_when_nothing_fits():
    # Если в бюджет не влез никто — не «не найдено», а ближайшее (всех ранжируем).
    sel = ProductSelector(segment="эконом")
    spec = ProductSpec(appliance=ApplianceType.OVEN, max_price=10000)
    cands = [_p("A", 40000), _p("B", 90000)]
    ranked = sel.ranked_candidates(spec, cands)
    assert ranked and ranked[0].price == 40000  # эконом → самый дешёвый из доступных


def test_max_price_coerced_from_messy_string():
    assert ProductSpec(max_price="30 000 руб").max_price == 30000
    assert ProductSpec(max_price="до 25000").max_price == 25000
    assert ProductSpec(max_price=None).max_price is None
    assert ProductSpec(max_price="").max_price is None


def test_candidates_without_price_excluded():
    sel = ProductSelector(segment="средний")
    cands = [_p("A", None), _p("B", 150)]
    ranked = sel.ranked_candidates(ProductSpec(), cands)
    assert [c.price for c in ranked] == [150]


def test_select_one_returns_unmatched_when_empty():
    sel = ProductSelector(segment="средний")
    item = sel.select_one(ProductSpec(), [_p("A", None)])
    assert item.product is None
    assert item.is_matched is False


# ── #1 Позитивная валидация: отсев явных противоречий ────────────────────────

def test_color_declension_normalized():
    # LLM присылает цвет в роде, согласованном с техникой — нормализуем по стему.
    assert _canon_color("чёрная") == "чёрный"
    assert _canon_color("чёрное") == "чёрный"
    assert _canon_color("белая") == "белый"
    assert _canon_color("серую") == "серый"
    assert _canon_color("нержавеющая сталь") == "нержавейка"


def test_feminine_color_matches_masculine_product():
    # spec.color="чёрная" должен подойти к товару «...BLACK» (в названии «чёрный»).
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.HOOD, color="чёрная")
    black = _p("Вытяжка Lex HOGAN 600 BLACK", 100)
    white = _p("Вытяжка Lex HOGAN 600 WHITE", 120)
    ranked = sel.ranked_candidates(spec, [black, white])
    assert [c.title for c in ranked] == ["Вытяжка Lex HOGAN 600 BLACK"]


def test_fuel_declension_normalized():
    # LLM присылает топливо в роде, согласованном с техникой — нормализуем по стему.
    assert _canon_fuel("газовый") == "газовая"
    assert _canon_fuel("газовая") == "газовая"
    assert _canon_fuel("электрический") == "электрическая"
    assert _canon_fuel("индукционный") == "индукционная"


def test_masculine_fuel_request_matches_product():
    # spec.fuel_type="газовый" должен подойти к «газовой» панели, а «электрическую» отсечь.
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.COOKTOP, fuel_type="газовый")
    gas = _p("Варочная панель газовая", 100)
    electric = _p("Варочная панель электрическая", 120)
    ranked = sel.ranked_candidates(spec, [gas, electric])
    assert [c.title for c in ranked] == ["Варочная панель газовая"]


def test_contradicting_color_dropped():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.OVEN, color="белый")
    ranked = sel.ranked_candidates(spec, [_p("Духовка чёрная", 100), _p("Духовка белая", 120)])
    assert [c.title for c in ranked] == ["Духовка белая"]


def test_silent_title_not_dropped():
    # Цвет не упомянут в названии — это не противоречие, кандидат остаётся.
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.OVEN, color="белый")
    ranked = sel.ranked_candidates(spec, [_p("Духовка ABC-123", 100)])
    assert len(ranked) == 1


def test_contradicting_fuel_dropped_gas_vs_induction():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.COOKTOP, fuel_type="газовая")
    ranked = sel.ranked_candidates(
        spec, [_p("Панель индукционная", 100), _p("Панель газовая", 120)]
    )
    assert [c.title for c in ranked] == ["Панель газовая"]


def test_fuel_lenient_within_electric_family():
    # «электрическая» запрошена, в названии «индукционная» — не считаем конфликтом.
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.COOKTOP, fuel_type="электрическая")
    ranked = sel.ranked_candidates(spec, [_p("Панель индукционная", 100)])
    assert len(ranked) == 1


def test_contradicting_burners_dropped():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.COOKTOP, burners=4)
    ranked = sel.ranked_candidates(
        spec, [_p("Панель на 2 конфорки", 100), _p("Панель 4 конфорки", 120)]
    )
    assert [c.title for c in ranked] == ["Панель 4 конфорки"]


def test_color_synonym_canonicalized():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.OVEN, color="нержавеющая сталь")
    ranked = sel.ranked_candidates(spec, [_p("Духовка чёрная", 100), _p("Духовка нерж", 120)])
    assert [c.title for c in ranked] == ["Духовка нерж"]


# ── #5 Взвешенный скоринг: совпадение характеристик важнее цены ──────────────

def test_attribute_match_outranks_better_price():
    # Запрошен белый. Молчаливый товар идеален по цене (медиана), но товар с
    # подтверждённым цветом всё равно должен идти первым.
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.OVEN, color="белый")
    cands = [_p("Духовка белая", 100), _p("Духовка XYZ", 150), _p("Духовка QWE", 200)]
    ranked = sel.ranked_candidates(spec, cands)
    assert ranked[0].title == "Духовка белая"
    assert len(ranked) == 3  # молчаливые не выброшены, только переранжированы
