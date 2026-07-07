"""Тесты движка сборки комплекта одного бренда (Ф4 проекта комплектов)."""

from __future__ import annotations

from max_ekrom_bot.matching.kit_builder import (
    build_brand_kit, choose_best_kit, _design_consistency,
)
from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec

OVEN, COOKTOP, HOOD = ApplianceType.OVEN, ApplianceType.COOKTOP, ApplianceType.HOOD


def _spec(appliance):
    return ProductSpec(appliance=appliance, raw_text=appliance.value)


def _p(brand, kind, price, color="чёрный"):
    return CatalogProduct(title=f"{kind} {brand} MODEL {color}", url=f"u/{brand}/{kind}/{price}",
                          price=price)


CASCADE = {"mechanic": "cascade_cheapest", "tiers": {2: 25, 3: 50}, "requires_oven": True,
           "slug": "casc", "gift": None}
FLAT = {"mechanic": "flat_combo", "tiers": {2: 5, 3: 7}, "requires_oven": False,
        "slug": "flat", "gift": None}


def test_build_brand_kit_full_coverage_and_cascade():
    specs = [_spec(OVEN), _spec(COOKTOP), _spec(HOOD)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40000), _p("Midea", "Духовой шкаф", 35000)],
        [_p("Korting", "Варочная панель", 25000)],
        [_p("Korting", "Вытяжка", 15000)],
    ]
    kit = build_brand_kit("Korting", specs, cands, [CASCADE])
    assert kit.coverage == 3 and kit.is_full
    assert kit.combo.mechanic == "cascade_cheapest"
    # 3 позиции → 50% на наименьший (вытяжка 15000) = 7500.
    assert kit.combo.discount_total == 7500.0
    assert kit.design_consistent is True  # все «чёрные» → одна семья


def test_build_brand_kit_filters_by_brand():
    specs = [_spec(OVEN), _spec(COOKTOP)]
    cands = [
        [_p("Midea", "Духовой шкаф", 30000)],          # нужного бренда нет
        [_p("Korting", "Варочная панель", 25000)],
    ]
    kit = build_brand_kit("Korting", specs, cands, [CASCADE])
    assert kit.coverage == 1 and not kit.is_full
    assert kit.selection.items[0].product is None       # духовку Korting не нашли


def test_design_inconsistent_mixed_colors():
    prods = [_p("Korting", "Духовой шкаф", 40000, color="чёрный"),
             _p("Korting", "Вытяжка", 15000, color="белый")]
    ok, note = _design_consistency(prods)
    assert ok is False and "разные цвета" in note.lower()


def test_choose_best_kit_prefers_full_coverage():
    specs = [_spec(OVEN), _spec(COOKTOP), _spec(HOOD)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40000), _p("Midea", "Духовой шкаф", 35000)],
        [_p("Korting", "Варочная панель", 25000), _p("Midea", "Варочная панель", 24000)],
        [_p("Korting", "Вытяжка", 15000)],  # вытяжка только Korting
    ]
    camp = {"Korting": [CASCADE], "Midea": [FLAT]}.get
    best = choose_best_kit(specs, cands, camp, brands=("Korting", "Midea"))
    assert best.brand == "Korting" and best.is_full  # Midea закрывает лишь 2/3


def test_choose_best_kit_prefers_bigger_benefit_when_tied():
    # Оба бренда покрывают всё и согласованы по цвету → решает выгода акции.
    specs = [_spec(OVEN), _spec(COOKTOP)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40000), _p("Midea", "Духовой шкаф", 40000)],
        [_p("Korting", "Варочная панель", 20000), _p("Midea", "Варочная панель", 20000)],
    ]
    # Korting каскад: 25% на наименьший 20000 = 5000. Midea flat: 5% от 60000 = 3000.
    camp = {"Korting": [CASCADE], "Midea": [FLAT]}.get
    best = choose_best_kit(specs, cands, camp, brands=("Korting", "Midea"))
    assert best.brand == "Korting" and best.combo.discount_total == 5000.0


def test_choose_best_kit_gift_beats_discount():
    specs = [_spec(OVEN), _spec(COOKTOP)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40000), _p("Midea", "Духовой шкаф", 40000)],
        [_p("Korting", "Варочная панель", 20000), _p("Midea", "Варочная панель", 20000)],
    ]
    gift_camp = {"mechanic": "gift", "requires_oven": False, "slug": "g",
                 "gift": {"condition": "sum", "options": {50000: "X"}}}
    camp = {"Korting": [CASCADE], "Midea": [gift_camp]}.get

    def gift_value(campaign, items):  # подарок на 9000 (дороже скидки 5000)
        return ("СВЧ в подарок", 9000)

    best = choose_best_kit(specs, cands, camp, brands=("Korting", "Midea"),
                           gift_value=gift_value)
    assert best.brand == "Midea" and best.combo.gift_title == "СВЧ в подарок"
    assert best.combo.discount_total == 9000.0


def test_choose_best_kit_lex_without_promo():
    # Lex без акций (campaigns → []): итог по прайсу, но бренд валиден.
    specs = [_spec(OVEN), _spec(COOKTOP)]
    cands = [[_p("Lex", "Духовой шкаф", 30000)], [_p("Lex", "Варочная панель", 18000)]]
    best = choose_best_kit(specs, cands, lambda b: [], brands=("Lex",))
    assert best.brand == "Lex" and best.is_full
    assert best.combo.applied is False and best.combo.final_total == 48000.0


def test_choose_best_kit_none_when_no_coverage():
    specs = [_spec(OVEN)]
    cands = [[_p("Bosch", "Духовой шкаф", 30000)]]  # не наш бренд
    assert choose_best_kit(specs, cands, lambda b: []) is None


def test_kit_is_strictly_single_color():
    # У бренда есть и чёрные, и белые — комплект должен быть ОДНОГО цвета.
    from max_ekrom_bot.matching.selector import _product_colors

    specs = [_spec(OVEN), _spec(COOKTOP)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40000, color="чёрный"),
         _p("Korting", "Духовой шкаф", 38000, color="белый")],
        [_p("Korting", "Варочная панель", 25000, color="белый"),
         _p("Korting", "Варочная панель", 26000, color="чёрный")],
    ]
    kit = build_brand_kit("Korting", specs, cands, [CASCADE])
    assert kit.coverage == 2 and kit.design_consistent is True
    colorsets = [_product_colors(m.product) for m in kit.selection.matched]
    assert set.intersection(*colorsets)  # есть общий цвет у всех позиций
    assert "Единый цвет" in kit.design_note


def test_kit_never_mixes_colors_even_if_partial():
    # Духовка только чёрная, панель только белая → не смешиваем: одноцветный, но неполный.
    specs = [_spec(OVEN), _spec(COOKTOP)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40000, color="чёрный")],
        [_p("Korting", "Варочная панель", 25000, color="белый")],
    ]
    kit = build_brand_kit("Korting", specs, cands, [CASCADE])
    assert kit.coverage == 1  # взяли одну позицию, а не разноцветную пару
