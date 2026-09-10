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


# ── Конфликт подтипов: кофемашина vs кофеварка, камера vs ларь ───────────────

def _spec_raw(text: str) -> ProductSpec:
    return ProductSpec(appliance=ApplianceType.UNKNOWN, raw_text=text)


def test_coffee_machine_request_drops_coffee_makers():
    sel = ProductSelector(segment="средний")
    cands = [_p("Кофеварка Vard VCPA1V", 100), _p("Кофемашина Jacky's JC BB45C", 120)]
    ranked = sel.ranked_candidates(_spec_raw("кофемашина автоматическая"), cands)
    assert [c.title for c in ranked] == ["Кофемашина Jacky's JC BB45C"]


def test_coffee_maker_request_drops_machines():
    sel = ProductSelector(segment="средний")
    cands = [_p("Кофеварка Vard VCPA1V", 100), _p("Кофемашина Jacky's JC BB45C", 120)]
    ranked = sel.ranked_candidates(_spec_raw("кофеварка капельная"), cands)
    assert [c.title for c in ranked] == ["Кофеварка Vard VCPA1V"]


def test_freezer_chamber_request_drops_chest_freezers():
    sel = ProductSelector(segment="средний")
    cands = [_p("Морозильный ларь Бирюса 200VK", 100), _p("Морозильная камера Korting KNFR", 120)]
    ranked = sel.ranked_candidates(_spec_raw("морозильная камера"), cands)
    assert [c.title for c in ranked] == ["Морозильная камера Korting KNFR"]


def test_chest_freezer_request_drops_chambers():
    sel = ProductSelector(segment="средний")
    cands = [_p("Морозильный ларь Бирюса 200VK", 100), _p("Морозильная камера Korting KNFR", 120)]
    ranked = sel.ranked_candidates(_spec_raw("морозильный ларь"), cands)
    assert [c.title for c in ranked] == ["Морозильный ларь Бирюса 200VK"]


def test_silent_subtype_title_not_dropped():
    # Название без обоих стемов пары («Suprema X100») — молчание не конфликт.
    sel = ProductSelector(segment="средний")
    cands = [_p("Suprema X100 espresso", 100)]
    ranked = sel.ranked_candidates(_spec_raw("кофемашина автоматическая"), cands)
    assert len(ranked) == 1


def test_no_subtype_in_request_keeps_everything():
    # Клиент не называл подтип — не отсеиваем ничего.
    sel = ProductSelector(segment="средний")
    cands = [_p("Морозильный ларь Бирюса", 100), _p("Морозильная камера Korting", 120)]
    ranked = sel.ranked_candidates(_spec_raw("морозилка в гараж"), cands)
    assert len(ranked) == 2


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


def test_width_hard_filter_drops_mismatch():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.OVEN, width_mm=600)
    narrow = CatalogProduct(
        title="Духовка", url="u/narrow", price=90, attributes={"Ширина, мм": "450"}
    )
    ok = CatalogProduct(
        title="Духовка", url="u/ok", price=100, attributes={"Ширина, мм": "595"}
    )
    ranked = sel.ranked_candidates(spec, [narrow, ok])
    assert len(ranked) == 1
    assert ranked[0].url == "u/ok"


def test_width_within_tolerance_kept():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.OVEN, width_mm=600)
    p = CatalogProduct(title="Духовка", url="u/ok", price=100, attributes={"Ширина, мм": "592"})
    assert sel.ranked_candidates(spec, [p])


def test_subtype_conflict_from_attributes():
    sel = ProductSelector(segment="средний")
    maker = CatalogProduct(
        title="Suprema X100",
        url="u/maker",
        price=100,
        attributes={"Тип": "Кофеварка капельная"},
    )
    machine = CatalogProduct(
        title="Jacky's JC BB45C",
        url="u/machine",
        price=120,
        attributes={"Тип": "Кофемашина автоматическая"},
    )
    ranked = sel.ranked_candidates(
        _spec_raw("кофемашина автоматическая"), [maker, machine]
    )
    assert len(ranked) == 1
    assert ranked[0].url == "u/machine"


def test_ivory_cream_not_matched_as_white():
    # «Слоновая кость»/IVORY — кремовый (семья бежевого), под «белый» не подходит.
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.HOOD, color="белый")
    ivory = CatalogProduct(title="Вытяжка Lex HUBBLE 500 IVORY", url="u1", price=100,
                           attributes={"Цвет": "Слоновая кость"})
    white = CatalogProduct(title="Вытяжка Lex SIMPLE 600 WHITE", url="u2", price=120,
                           attributes={"Цвет": "Белый"})
    ranked = sel.ranked_candidates(spec, [ivory, white])
    assert [c.title for c in ranked] == ["Вытяжка Lex SIMPLE 600 WHITE"]


# ── Смягчение размеров + комби-СВЧ (хвосты подбора, диагностика на проде) ──────

def _cook(title, price, burners_attr=None):
    from max_ekrom_bot.models import CatalogProduct
    attrs = {"Количество конфорок": burners_attr} if burners_attr else {}
    return CatalogProduct(title=title, url=title, price=price, attributes=attrs)


def test_burners_soft_when_no_exact_match():
    # «3 конфорки» точных нет → не «не найдено», а ближайшее (4 конф).
    from max_ekrom_bot.models import ApplianceType, ProductSpec
    from max_ekrom_bot.matching.selector import ProductSelector
    spec = ProductSpec(appliance=ApplianceType.COOKTOP, burners=3, raw_text="варка 3 конфорки")
    cands = [_cook("Варочная панель A", 20000, "4 конфорки"),
             _cook("Варочная панель B", 18000, "2 конфорки")]
    ranked = ProductSelector().ranked_candidates(spec, cands)
    assert len(ranked) == 2                       # смягчилось, выдача не пустая


def test_width_soft_when_no_match():
    # «духовка 450» (высота компакта) не должна обнулять выдачу по ширине.
    from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec
    from max_ekrom_bot.matching.selector import ProductSelector
    spec = ProductSpec(appliance=ApplianceType.OVEN, width_mm=450, raw_text="духовка 450")
    cands = [CatalogProduct(title="Духовой шкаф A", url="a", price=40000,
                            attributes={"Ширина, мм": "595"}),
             CatalogProduct(title="Духовой шкаф B", url="b", price=35000,
                            attributes={"Ширина, мм": "595"})]
    ranked = ProductSelector().ranked_candidates(spec, cands)
    assert len(ranked) == 2                       # не 0 из-за строгой ширины 450


def test_combi_microwave_oven_excluded_from_plain_oven():
    from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec
    from max_ekrom_bot.matching.selector import ProductSelector
    spec = ProductSpec(appliance=ApplianceType.OVEN, raw_text="духовой шкаф")
    plain = CatalogProduct(title="Духовой шкаф Midea MO60000", url="p", price=40000)
    combi = CatalogProduct(title="Духовой шкаф Midea MCO82007 с функцией СВЧ", url="c",
                           price=76990, attributes={"Функция СВЧ": "Да"})
    ranked = ProductSelector().ranked_candidates(spec, [combi, plain])
    assert [p.url for p in ranked] == ["p"]       # комби-СВЧ отсеян под простой духовой


def test_combi_microwave_oven_kept_when_requested():
    from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec
    from max_ekrom_bot.matching.selector import ProductSelector
    spec = ProductSpec(appliance=ApplianceType.OVEN, raw_text="духовой с СВЧ 2в1")
    combi = CatalogProduct(title="Духовой шкаф Maunfeld с функцией СВЧ", url="c",
                           price=60000, attributes={"Функция СВЧ": "Да"})
    ranked = ProductSelector().ranked_candidates(spec, [combi])
    assert [p.url for p in ranked] == ["c"]       # просили СВЧ → комби подходит


def test_combi_oven_by_title_and_attr():
    from max_ekrom_bot.models import CatalogProduct
    from max_ekrom_bot.matching.selector import _is_combi_microwave_oven
    assert _is_combi_microwave_oven(CatalogProduct(title="Духовой шкаф с СВЧ", url="a"))
    assert _is_combi_microwave_oven(CatalogProduct(title="Духовой X", url="b",
                                    attributes={"Функция СВЧ": "Да"}))
    assert not _is_combi_microwave_oven(CatalogProduct(title="Духовой шкаф Korting OKB", url="c"))


# ── Порядок смягчений: тип техники важнее габарита ────────────────────────────

def test_named_type_beats_width_when_nothing_fits_exactly():
    """Живой провал: «вытяжка полновстр. 450» → островная за 92 990 ₽.

    У бренда нет полновстраиваемой ровно на 450: есть островная 450 и
    полновстраиваемая 495. Клиент назвал ТИП — гнуться должен габарит.
    """
    from max_ekrom_bot.models import AttrConstraint

    spec = ProductSpec(
        appliance=ApplianceType.HOOD, raw_text="вытяжка полновстр. 450", width_mm=450,
        attr_constraints=[AttrConstraint(attribute="Тип кухонной вытяжки", op="eq",
                                         value="Полновстраиваемая")],
    )
    island = CatalogProduct(
        title="Кухонная вытяжка Korting KHA 45970 W CUBE", url="u1", price=92990,
        attributes={"Тип кухонной вытяжки": "Островная", "Ширина, мм": "450"},
    )
    built_in = CatalogProduct(
        title="Кухонная вытяжка Korting KHI 6631 RN", url="u2", price=13690,
        attributes={"Тип кухонной вытяжки": "Полновстраиваемая", "Ширина, мм": "495"},
    )
    ranked = ProductSelector().ranked_candidates(spec, [island, built_in])
    assert ranked, "выдача не должна быть пустой"
    assert "KHI 6631" in ranked[0].title, "взяли островную вместо полновстраиваемой"


def test_exact_match_still_wins_when_it_exists():
    # Контроль: если есть полновстраиваемая нужной ширины — берём именно её.
    from max_ekrom_bot.models import AttrConstraint

    spec = ProductSpec(
        appliance=ApplianceType.HOOD, raw_text="вытяжка полновстраиваемая 450", width_mm=450,
        attr_constraints=[AttrConstraint(attribute="Тип кухонной вытяжки", op="eq",
                                         value="Полновстраиваемая")],
    )
    exact = CatalogProduct(
        title="Вытяжка BAY 45", url="u1", price=7901,
        attributes={"Тип кухонной вытяжки": "Полновстраиваемая", "Ширина, мм": "450"},
    )
    wider = CatalogProduct(
        title="Вытяжка KHI 6631", url="u2", price=13690,
        attributes={"Тип кухонной вытяжки": "Полновстраиваемая", "Ширина, мм": "495"},
    )
    ranked = ProductSelector().ranked_candidates(spec, [wider, exact])
    assert "BAY 45" in ranked[0].title


def test_width_still_filters_when_type_not_requested():
    # Без названного типа ширина остаётся жёстким фильтром (ПММ 45 ≠ 60).
    spec = ProductSpec(appliance=ApplianceType.DISHWASHER,
                       raw_text="посудомойка 450", width_mm=450)
    narrow = CatalogProduct(title="ПММ 45", url="u1", price=30000,
                            attributes={"Ширина, мм": "450"})
    wide = CatalogProduct(title="ПММ 60", url="u2", price=25000,
                          attributes={"Ширина, мм": "600"})
    ranked = ProductSelector().ranked_candidates(spec, [wide, narrow])
    assert ranked[0].title == "ПММ 45"       # дешевле, но шире — не берём


# ── Тип вытяжки распознаётся без LLM ─────────────────────────────────────────

def _hood(kind, price=10000):
    from max_ekrom_bot.models import CatalogProduct
    return CatalogProduct(title=f"Вытяжка {kind}", url=kind, price=price,
                          attributes={"Тип кухонной вытяжки": kind})


def test_hood_type_filter_without_llm_constraints():
    # Требование клиента не должно зависеть от LLM-маппера: когда он вернул
    # битый JSON, ограничений не было и на «скошенная или труба» уходила
    # ПОЛНОВСТРАИВАЕМАЯ. Тип берём из текста запроса.
    from max_ekrom_bot.matching.selector import ProductSelector
    from max_ekrom_bot.models import ProductSpec

    spec = ProductSpec(raw_text="вытяжка отдельная или скошенная или труба")
    sel = ProductSelector()
    assert sel.passes_filters(spec, _hood("Наклонная")) is True
    assert sel.passes_filters(spec, _hood("Настенная-тубус")) is True
    assert sel.passes_filters(spec, _hood("Купольная")) is True
    assert sel.passes_filters(spec, _hood("Полновстраиваемая")) is False
    assert sel.passes_filters(spec, _hood("Встраиваемая")) is False


def test_hood_type_builtin_requested():
    from max_ekrom_bot.matching.selector import ProductSelector
    from max_ekrom_bot.models import ProductSpec

    spec = ProductSpec(raw_text="вытяжка полновстраиваемая 600")
    sel = ProductSelector()
    assert sel.passes_filters(spec, _hood("Полновстраиваемая")) is True
    assert sel.passes_filters(spec, _hood("Купольная")) is False


def test_hood_type_absent_when_not_asked():
    # Тип не назван — фильтра нет, подходит любая вытяжка.
    from max_ekrom_bot.matching.selector import ProductSelector
    from max_ekrom_bot.models import ProductSpec

    spec = ProductSpec(raw_text="вытяжка 600")
    sel = ProductSelector()
    for kind in ("Наклонная", "Полновстраиваемая", "Купольная"):
        assert sel.passes_filters(spec, _hood(kind)) is True


def test_hood_filter_not_applied_to_other_appliances():
    # Слово «островная» в запросе про другую технику не должно ничего ломать.
    from max_ekrom_bot.matching.selector import _wanted_hood_types

    assert _wanted_hood_types("духовой шкаф и варочная панель") == ()
    assert "наклонная" in _wanted_hood_types("вытяжка наклонная")


# ── Продаваемость: наличие и витринный образец ───────────────────────────────

def _prod(title="Духовой шкаф Korting X", price=30000, **kw):
    from max_ekrom_bot.models import CatalogProduct
    return CatalogProduct(title=title, url=title, price=price, **kw)


def test_out_of_stock_never_passes_filters():
    # Контрольная сборка 10.09: 16 из 50 позиций комплектов — «нет в наличии».
    # Кэш отдаёт всё подряд, поэтому отсекаем в селекторе — единственной точке,
    # через которую идут все источники кандидатов.
    from max_ekrom_bot.matching.selector import ProductSelector, is_sellable
    from max_ekrom_bot.models import ProductSpec

    spec = ProductSpec(raw_text="духовой шкаф")
    sel = ProductSelector()
    assert is_sellable(_prod(in_stock=False)) is False
    assert sel.passes_filters(spec, _prod(in_stock=False)) is False
    assert sel.passes_filters(spec, _prod(in_stock=True)) is True


def test_unknown_stock_is_not_treated_as_out_of_stock():
    # Статус не прочитан (None) — выдумывать отсутствие нельзя.
    from max_ekrom_bot.matching.selector import ProductSelector, is_sellable
    from max_ekrom_bot.models import ProductSpec

    assert is_sellable(_prod(in_stock=None)) is True
    assert ProductSelector().passes_filters(ProductSpec(raw_text="духовой шкаф"),
                                            _prod(in_stock=None)) is True


def test_showroom_sample_is_dropped_but_available_sample_is_not():
    # «Витринный образец» — сам товар выставочный, отбрасываем.
    # «Есть витринный образец» — обычный товар с экземпляром в шоуруме, продаём.
    from max_ekrom_bot.matching.selector import ProductSelector, is_sellable
    from max_ekrom_bot.models import ProductSpec

    spec = ProductSpec(raw_text="духовой шкаф")
    sel = ProductSelector()
    sample = _prod(in_stock=True, attributes={"Витринный образец": "Да"})
    has_sample = _prod(in_stock=True, attributes={"Есть витринный образец": "Да"})
    assert is_sellable(sample) is False and sel.passes_filters(spec, sample) is False
    assert is_sellable(has_sample) is True and sel.passes_filters(spec, has_sample) is True


def test_sellability_survives_relaxation():
    # Смягчения снимают требования по цвету/размеру ради полноты комплекта, но
    # наличие снять нельзя: ranked_candidates не должен вернуть отсутствующий
    # товар даже когда других нет.
    from max_ekrom_bot.matching.selector import ProductSelector
    from max_ekrom_bot.models import ProductSpec

    spec = ProductSpec(raw_text="духовой шкаф чёрный 600", color="чёрный", width_mm=600)
    only_oos = [_prod(in_stock=False, attributes={"Цвет": "Чёрный", "Ширина, мм": "600"})]
    assert ProductSelector().ranked_candidates(spec, only_oos) == []


def test_bestseller_flag_detection():
    from max_ekrom_bot.matching.selector import is_bestseller

    assert is_bestseller(_prod(attributes={"Хит продаж": "Да"})) is True
    assert is_bestseller(_prod(attributes={"Хит продаж": ""})) is False
    assert is_bestseller(_prod(attributes={})) is False
