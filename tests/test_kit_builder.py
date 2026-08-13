"""Тесты движка сборки комплекта одного бренда (Ф4 проекта комплектов)."""

from __future__ import annotations

from max_ekrom_bot.matching.kit_builder import (
    build_brand_kit, choose_best_kit, client_brands, split_out_of_scope,
    _design_consistency,
)
from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec

OVEN, COOKTOP, HOOD = ApplianceType.OVEN, ApplianceType.COOKTOP, ApplianceType.HOOD
FRIDGE = ApplianceType.FRIDGE


def _spec(appliance, **kw):
    return ProductSpec(appliance=appliance, raw_text=appliance.value, **kw)


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


def test_kit_colors_within_one_group():
    # У бренда есть и чёрные, и белые — все позиции комплекта из ОДНОЙ цветовой группы.
    from max_ekrom_bot.matching.kit_builder import _COLOR_GROUPS
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
    colors: set = set()
    for m in kit.selection.matched:
        colors |= _product_colors(m.product)
    assert any(colors <= g for g in _COLOR_GROUPS)  # цвета укладываются в одну группу


def test_kit_combines_compatible_colors():
    # Чёрный + нержавейка сочетаются (сталь — нейтральный мостик) → собираются вместе.
    from max_ekrom_bot.matching.selector import _product_colors

    specs = [_spec(OVEN), _spec(COOKTOP), _spec(HOOD)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40000, color="чёрный")],
        [_p("Korting", "Варочная панель", 25000, color="нержавейка")],
        [_p("Korting", "Вытяжка", 15000, color="чёрный")],
    ]
    kit = build_brand_kit("Korting", specs, cands, [CASCADE])
    assert kit.coverage == 3
    colors: set = set()
    for m in kit.selection.matched:
        colors |= _product_colors(m.product)
    assert "нержавейка" in colors and "чёрный" in colors
    assert "Сочетающиеся цвета" in kit.design_note


def test_kit_never_mixes_incompatible_colors():
    # Духовка только чёрная, панель только белая (разные группы) → не смешиваем.
    specs = [_spec(OVEN), _spec(COOKTOP)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40000, color="чёрный")],
        [_p("Korting", "Варочная панель", 25000, color="белый")],
    ]
    kit = build_brand_kit("Korting", specs, cands, [CASCADE])
    assert kit.coverage == 1  # взяли одну позицию, а не несочетаемую пару


# ── Калибровка по оплаченным подборкам: цвет клиента, спец-модель, охват ───────

def test_client_colors_override_groups():
    # Эталон ЕкромКухонька: «духовка БЕЛАЯ + варка ЧЁРНАЯ» — цвета клиента соблюдаются,
    # хотя они из разных цветовых групп.
    specs = [_spec(OVEN, color="белый"), _spec(COOKTOP, color="чёрный")]
    cands = [
        [_p("Maunfeld", "Духовой шкаф", 36000, color="белый"),
         _p("Maunfeld", "Духовой шкаф", 34000, color="чёрный")],
        [_p("Maunfeld", "Варочная панель", 11000, color="чёрный"),
         _p("Maunfeld", "Варочная панель", 12000, color="белый")],
    ]
    kit = build_brand_kit("Maunfeld", specs, cands, [FLAT])
    assert kit.coverage == 2
    titles = [m.product.title for m in kit.selection.matched]
    assert "белый" in titles[0] and "чёрный" in titles[1]
    assert kit.design_consistent is True
    assert "по вашему запросу" in kit.design_note


def test_partial_client_colors_note_by_request():
    # Часть позиций с явным цветом (белая духовка), часть без — пометка «по запросу»,
    # а не «Единый цвет» по бесцветной позиции (иначе теряла бы белую духовку).
    specs = [_spec(OVEN, color="белый"), _spec(HOOD)]
    cands = [
        [_p("Midea", "Духовой шкаф", 40000, color="белый")],
        [_p("Midea", "Вытяжка", 12000, color="чёрный")],
    ]
    kit = build_brand_kit("Midea", specs, cands, [FLAT])
    assert kit.coverage == 2
    assert "по вашему запросу" in kit.design_note


def test_prefers_special_fridge_model():
    # Акция «холодильник по спеццене»: движок сам берёт акционную модель RBN 1965,
    # хотя обычный выбор взял бы другой холодильник.
    spec_camp = {"mechanic": "spec_price", "requires_oven": True, "slug": "kup_spec",
                 "tiers": {}, "gift": None, "exclusions": [],
                 "spec": {"models": ["RBN 1761", "RBN 1965"],
                          "tiers": {3: 40000.0, 4: 27000.0}, "pct": None,
                          "line_label": None}}
    specs = [_spec(OVEN), _spec(COOKTOP), _spec(HOOD), _spec(FRIDGE)]
    cands = [
        [_p("Kuppersberg", "Духовой шкаф", 45000)],
        [_p("Kuppersberg", "Варочная панель", 25000)],
        [_p("Kuppersberg", "Вытяжка", 15000)],
        [_p("Kuppersberg", "Встраиваемый холодильник RBN 1765", 80396),
         _p("Kuppersberg", "Встраиваемый холодильник RBN 1965", 97281)],
    ]
    kit = build_brand_kit("Kuppersberg", specs, cands, [spec_camp])
    fridge = kit.selection.matched[3].product
    assert "RBN 1965" in fridge.title                    # взяли акционную модель
    assert kit.combo.mechanic == "spec_price" and kit.combo.applied
    assert kit.combo.discount_total == 97281 - 40000     # 3 предмета → фикс 40 000


def test_split_out_of_scope():
    specs = [
        _spec(OVEN),
        ProductSpec(appliance=ApplianceType.UNKNOWN, raw_text="мойка металл"),
        ProductSpec(appliance=ApplianceType.UNKNOWN, raw_text="смеситель 2 в 1 сатин"),
        ProductSpec(appliance=ApplianceType.COOKTOP, raw_text="варочная Smeg",
                    brand="Smeg"),
        ProductSpec(appliance=ApplianceType.DISHWASHER, raw_text="ПММ 450",
                    brand="Kuppersberg"),
    ]
    in_scope, manager = split_out_of_scope(specs)
    assert [s.raw_text for s in in_scope] == ["oven", "ПММ 450"]
    assert len(manager) == 3                             # мойка, смеситель, Smeg


def test_dishwasher_not_mistaken_for_sink():
    # «посудомойка» содержит «мойк», но это техника 5 брендов, не сантехника.
    specs = [
        ProductSpec(appliance=ApplianceType.DISHWASHER, raw_text="посудомойка 450"),
        ProductSpec(appliance=ApplianceType.UNKNOWN, raw_text="посудомойка 45 см"),
        ProductSpec(appliance=ApplianceType.UNKNOWN, raw_text="кухонная мойка нержавейка"),
    ]
    in_scope, manager = split_out_of_scope(specs)
    assert [s.raw_text for s in in_scope] == ["посудомойка 450", "посудомойка 45 см"]
    assert [s.raw_text for s in manager] == ["кухонная мойка нержавейка"]


def test_client_brands_from_message():
    specs = [_spec(OVEN)]
    assert client_brands(specs, "можно Kuppersberg Maunfeld: духовой + варка") == (
        "Maunfeld", "Kuppersberg",
    )
    assert client_brands(specs, "просто духовка и варка") == (
        "Korting", "Maunfeld", "Midea", "Lex", "Kuppersberg",
    )
    assert client_brands([_spec(OVEN, brand="Midea")], "") == ("Midea",)


def test_prefers_promo_gift_model_in_position():
    # Подарочная модель ПММ предпочитается в позиции, даже если она дороже обычной.
    gift_camp = {"mechanic": "gift", "requires_oven": False, "slug": "g", "tiers": {},
                 "exclusions": [], "spec": None,
                 "gift": {"condition": "count", "options": {2: "MID45S100I"}}}
    specs = [_spec(OVEN), _spec(COOKTOP), _spec(ApplianceType.DISHWASHER)]
    cands = [
        [_p("Midea", "Духовой шкаф", 40000)],
        [_p("Midea", "Варочная панель", 25000)],
        [_p("Midea", "Посудомоечная машина MID45S100I", 30000),
         _p("Midea", "Посудомоечная машина MID99X", 22000)],
    ]
    kit = build_brand_kit("Midea", specs, cands, [gift_camp])
    assert "MID45S100I" in kit.selection.matched[2].product.title


def test_choose_best_kit_none_when_all_foreign(tmp_path=None):
    specs = [_spec(OVEN)]
    cands = [[_p("Smeg", "Духовой шкаф", 90000)]]
    assert choose_best_kit(specs, cands, lambda b: []) is None


# Подарок-лестница Korting «Формула выгоды»: 2 прибора → вытяжка, 3 → духовой,
# 4 → ПММ. Выдаётся ровно ОДИН подарок — по максимальному достижимому порогу.
LADDER = {"mechanic": "gift", "tiers": {}, "requires_oven": False, "slug": "formula",
          "spec": None,
          "gift": {"condition": "count",
                   "options": {2: "KHI 6393", 3: "OKB 3810", 4: "KDI 45140"}}}


def test_promo_preference_uses_reachable_gift_tier_only():
    from max_ekrom_bot.matching.kit_builder import _preferred_promo_models

    # 3 позиции → подарок по порогу 3 (духовой шкаф), а не все три модели сразу.
    assert _preferred_promo_models([LADDER], 3) == ["OKB 3810"]
    assert _preferred_promo_models([LADDER], 2) == ["KHI 6393"]
    assert _preferred_promo_models([LADDER], 5) == ["KDI 45140"]
    # Порог не достигнут — предпочитать нечего.
    assert _preferred_promo_models([LADDER], 1) == []


def test_promo_preference_keeps_all_models_for_sum_gifts():
    from max_ekrom_bot.matching.kit_builder import _preferred_promo_models

    # Порог по СУММЕ покупки заранее не проверить — оставляем все модели.
    sum_gift = {"mechanic": "gift", "slug": "s", "spec": None,
                "gift": {"condition": "sum", "options": {10000: "KWK 0902", 25000: "KHB 0315"}}}
    assert _preferred_promo_models([sum_gift], 2) == ["KWK 0902", "KHB 0315"]


def test_kit_picks_gift_model_of_reachable_tier():
    # Клиент просит духовку/варочную/вытяжку (3 позиции). Подарок на этом пороге —
    # духовой шкаф OKB 3810: его и ставим, хотя вытяжка KHI 6393 тоже есть в пуле.
    specs = [_spec(OVEN), _spec(COOKTOP), _spec(HOOD)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40000),
         CatalogProduct(title="Духовой шкаф Korting OKB 3810 FGN чёрный",
                        url="u/korting/oven/okb", price=44000)],
        [_p("Korting", "Варочная панель", 25000)],
        [_p("Korting", "Вытяжка", 15000),
         CatalogProduct(title="Вытяжка Korting KHI 6393 N чёрный",
                        url="u/korting/hood/khi", price=19000)],
    ]
    kit = build_brand_kit("Korting", specs, cands, [LADDER])
    titles = [m.product.title for m in kit.selection.matched]
    assert any("OKB 3810" in t for t in titles)      # подарочный духовой — взят
    assert not any("KHI 6393" in t for t in titles)  # вытяжка порога 2 — не навязана
