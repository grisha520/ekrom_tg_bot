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

    # Подарочная модель займёт позицию и обнулится → порог обязан выполняться
    # ОСТАЛЬНЫМИ (positions−1) позициями. 3 позиции → 2 оплаченных → порог 2
    # (вытяжка), а не 3: движок расчёта откажет в обнулении духовки.
    assert _preferred_promo_models([LADDER], 3) == ["KHI 6393"]
    assert _preferred_promo_models([LADDER], 4) == ["OKB 3810"]
    assert _preferred_promo_models([LADDER], 5) == ["KDI 45140"]
    # 2 позиции: оплаченной останется 1 < порога 2 — предпочитать нечего.
    assert _preferred_promo_models([LADDER], 2) == []
    assert _preferred_promo_models([LADDER], 1) == []


def test_promo_preference_keeps_all_models_for_sum_gifts():
    from max_ekrom_bot.matching.kit_builder import _preferred_promo_models

    # Порог по СУММЕ покупки заранее не проверить — оставляем все модели.
    sum_gift = {"mechanic": "gift", "slug": "s", "spec": None,
                "gift": {"condition": "sum", "options": {10000: "KWK 0902", 25000: "KHB 0315"}}}
    assert _preferred_promo_models([sum_gift], 2) == ["KWK 0902", "KHB 0315"]


def test_kit_picks_gift_model_of_reachable_tier():
    # Клиент просит духовку/варочную/вытяжку (3 позиции). Обнулится подарок,
    # порог которого выполняют 2 ОПЛАЧЕННЫЕ позиции — это вытяжка KHI 6393
    # (порог 2). Духовку OKB 3810 (порог 3) навязывать нельзя: её обнуление
    # оставит 2 оплаченных < 3, и движок расчёта откажет.
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
    assert any("KHI 6393" in t for t in titles)      # вытяжка порога 2 — взята
    assert not any("OKB 3810" in t for t in titles)  # духовка порога 3 — не навязана


def test_over_budget_replacement_is_flagged():
    # E2E-прогон на проде: «духовка до 30000» молча закрывалась моделью за 40 990.
    # Замена допустима (полнота комплекта важнее), но обязана быть помечена.
    specs = [_spec(OVEN, max_price=30000), _spec(COOKTOP)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40990)],   # дешевле у бренда нет
        [_p("Korting", "Варочная панель", 12290)],
    ]
    kit = build_brand_kit("Korting", specs, cands, [])
    oven_item = kit.selection.matched[0]
    assert oven_item.product is not None
    assert "бюджет" in oven_item.reason.lower()
    # Позиция в бюджете — без пометки.
    assert kit.selection.matched[1].reason == ""


def test_within_budget_no_flag():
    specs = [_spec(OVEN, max_price=50000)]
    cands = [[_p("Korting", "Духовой шкаф", 40990)]]
    kit = build_brand_kit("Korting", specs, cands, [])
    assert kit.selection.matched[0].reason == ""


# ── Пометки о замене: тип/цвет/ширина, а не только бюджет ─────────────────────

def test_replacement_note_domino_not_found():
    # E2E на проде: «варочная панель домино» закрылась обычной 60-см панелью
    # молча. Замена допустима, но клиент обязан узнать об этом из сообщения.
    from max_ekrom_bot.matching.kit_builder import replacement_note

    spec = ProductSpec(appliance=COOKTOP, raw_text="варочная панель домино")
    wide = CatalogProduct(title="Варочная панель Korting HG 465 CTRN",
                          url="u", price=35990, attributes={"Ширина": "600 мм"})
    note = replacement_note(spec, wide)
    assert "домино" in note.lower()

    narrow = CatalogProduct(title="Варочная панель домино Korting HG 300",
                            url="u2", price=21000, attributes={"Ширина": "290 мм"})
    assert replacement_note(spec, narrow) == ""   # домино нашлось — молчим


def test_replacement_note_color_mismatch():
    from max_ekrom_bot.matching.kit_builder import replacement_note

    spec = ProductSpec(appliance=OVEN, raw_text="духовка белая", color="белый")
    black = CatalogProduct(title="Духовой шкаф Korting OKB 481 чёрный", url="u",
                           price=40000)
    assert "цвет" in replacement_note(spec, black).lower()
    white = CatalogProduct(title="Духовой шкаф Korting OKB 7951 CMW белый", url="u2",
                           price=42000)
    assert replacement_note(spec, white) == ""


def test_replacement_note_silent_without_data():
    # Характеристик нет и цвет в названии не заявлен — молчим, а не выдумываем.
    from max_ekrom_bot.matching.kit_builder import replacement_note

    spec = ProductSpec(appliance=OVEN, raw_text="духовка белая", color="белый")
    unknown = CatalogProduct(title="Духовой шкаф Korting OKB 481", url="u", price=40000)
    assert replacement_note(spec, unknown) == ""


def test_replacement_note_combines_reasons():
    from max_ekrom_bot.matching.kit_builder import replacement_note

    spec = ProductSpec(appliance=OVEN, raw_text="духовка белая до 30000",
                       color="белый", max_price=30000)
    prod = CatalogProduct(title="Духовой шкаф Korting OKB 481 чёрный", url="u",
                          price=40990)
    note = replacement_note(spec, prod)
    assert "бюджет" in note.lower() and "цвет" in note.lower()


def test_rivals_recorded_for_explanation():
    # Победитель хранит проигравшие бренды — из них строится объяснение выбора.
    specs = [_spec(OVEN), _spec(COOKTOP), _spec(HOOD)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40000), _p("Midea", "Духовой шкаф", 35000)],
        [_p("Korting", "Варочная панель", 25000), _p("Midea", "Варочная панель", 24000)],
        [_p("Korting", "Вытяжка", 15000)],          # вытяжка только у Korting
    ]
    camp = {"Korting": [CASCADE], "Midea": [FLAT]}.get
    best = choose_best_kit(specs, cands, camp, brands=("Korting", "Midea"))
    assert best.brand == "Korting"
    assert [r.brand for r in best.rivals] == ["Midea"]
    assert best.rivals[0].coverage == 2          # Midea закрыла лишь 2 из 3


def test_brand_choice_prefers_cheaper_kit_over_bigger_gift():
    # Живой провал (сообщение «духовка 450, варка, вытяжка полновстр., ПММ»):
    # Korting выигрывал подарком на 42 490 ₽, хотя его комплект стоил 222 460 ₽
    # против 88 133 ₽ у Maunfeld. Клиент переплачивал 134 тысячи за подарок на 42.
    specs = [_spec(OVEN), _spec(COOKTOP)]
    cands = [
        [_p("Korting", "Духовой шкаф", 150000), _p("Maunfeld", "Духовой шкаф", 60000)],
        [_p("Korting", "Варочная панель", 72000), _p("Maunfeld", "Варочная панель", 28000)],
    ]
    gift_camp = {"mechanic": "gift", "requires_oven": False, "slug": "formula",
                 "gift": {"condition": "count", "options": {2: "GIFT"}}}
    camp = {"Korting": [gift_camp], "Maunfeld": [FLAT]}.get

    def gift_42k(campaign, items):
        return ("ПММ в подарок", 42490)

    best = choose_best_kit(specs, cands, camp, brands=("Korting", "Maunfeld"),
                           gift_value=gift_42k)
    assert best.brand == "Maunfeld"
    # 88 000 − 5% = 83 600 против Korting 222 000 − подарок 42 490 = 179 510.
    assert best.combo.final_total < 100000


def test_gift_still_wins_when_prices_equal():
    # Обратная сторона: при равной цене подарок обязан побеждать — вычитаем его
    # стоимость из того, что клиент фактически отдаёт.
    specs = [_spec(OVEN), _spec(COOKTOP)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40000), _p("Midea", "Духовой шкаф", 40000)],
        [_p("Korting", "Варочная панель", 20000), _p("Midea", "Варочная панель", 20000)],
    ]
    gift_camp = {"mechanic": "gift", "requires_oven": False, "slug": "g",
                 "gift": {"condition": "sum", "options": {50000: "X"}}}
    camp = {"Korting": [CASCADE], "Midea": [gift_camp]}.get

    best = choose_best_kit(specs, cands, camp, brands=("Korting", "Midea"),
                           gift_value=lambda c, i: ("СВЧ в подарок", 9000))
    # Korting: 60 000 − 5 000 = 55 000. Midea: 60 000 − подарок 9 000 = 51 000.
    assert best.brand == "Midea"


def test_effective_total_accounts_for_gift_on_top():
    from max_ekrom_bot.matching.combo_pricing import ComboResult
    from max_ekrom_bot.matching.kit_builder import BrandKit
    from max_ekrom_bot.models import SelectionResult

    on_top = ComboResult(mechanic="gift", subtotal=100000, discount_total=30000,
                         final_total=100000, applied=True, gift_title="ПММ")
    kit = BrandKit(brand="K", selection=SelectionResult(items=[]), combo=on_top,
                   requested=2, coverage=2, design_consistent=True)
    assert kit.effective_total == 70000        # подарок сверху вычитается

    # Скидка уже сидит в итоге — второй раз не вычитаем.
    discounted = ComboResult(mechanic="flat_combo", subtotal=100000,
                             discount_total=10000, final_total=90000, applied=True)
    kit2 = BrandKit(brand="K", selection=SelectionResult(items=[]), combo=discounted,
                    requested=2, coverage=2, design_consistent=True)
    assert kit2.effective_total == 90000


def test_colorless_candidate_still_closes_position():
    """Живой провал: варочная выпадала у ВСЕХ пяти брендов при подходящих кандидатах.

    Сборка идёт по цветовой группе, а у карточек живого обхода атрибута «Цвет»
    нет и по названию он не читается («HI 64013 B» — односимвольный код мы не
    трактуем). Раньше такие товары отбрасывались, и позиция оставалась пустой.
    """
    specs = [_spec(OVEN), _spec(COOKTOP)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40000, color="чёрный")],
        [CatalogProduct(title="Индукционная варочная панель Korting HI 64013 B",
                        url="u/cook", price=28490)],       # цвет не распознаётся
    ]
    kit = build_brand_kit("Korting", specs, cands, [])
    assert kit.coverage == 2, "позиция без распознанного цвета не должна выпадать"
    assert "HI 64013" in kit.selection.matched[1].product.title


def test_group_color_still_preferred_over_colorless():
    # Контроль: если есть товар нужной гаммы — он идёт вперёд «молчащего».
    specs = [_spec(OVEN), _spec(COOKTOP)]
    cands = [
        [_p("Korting", "Духовой шкаф", 40000, color="чёрный")],
        [CatalogProduct(title="Варочная панель Korting БЕЗ ЦВЕТА", url="u/x", price=10000),
         _p("Korting", "Варочная панель", 26000, color="чёрный")],
    ]
    kit = build_brand_kit("Korting", specs, cands, [])
    assert "чёрный" in kit.selection.matched[1].product.title.lower()


def test_no_replacement_note_for_same_install_family():
    # «встроенная» (клиент) и «Встраиваемый» (каталог) — одно и то же: сверка по
    # первым буквам давала ложную пометку о замене.
    from max_ekrom_bot.matching.kit_builder import replacement_note

    spec = ProductSpec(appliance=ApplianceType.MICROWAVE,
                       raw_text="свч встроенная 600", install_type="встроенная")
    product = CatalogProduct(title="Микроволновая печь Midea MI10257GW", url="u",
                             price=30990, attributes={"Тип установки": "Встраиваемый"})
    assert replacement_note(spec, product) == ""


def test_replacement_note_when_install_family_differs():
    # Настоящее расхождение (просили встраиваемый — нашёлся отдельностоящий)
    # по-прежнему помечается.
    from max_ekrom_bot.matching.kit_builder import replacement_note

    spec = ProductSpec(appliance=ApplianceType.FRIDGE,
                       raw_text="встраиваемый холодильник", install_type="встраиваемый")
    product = CatalogProduct(title="Холодильник Midea X", url="u", price=42990,
                             attributes={"Тип установки": "Отдельностоящий"})
    note = replacement_note(spec, product)
    assert "установка" in note and "тдельностоящ" in note


def test_no_note_when_install_type_is_not_about_mounting():
    # «отдельная или скошенная или труба» — это про ТИП вытяжки, а не установку:
    # семьи не распознаются, ложной пометки быть не должно.
    from max_ekrom_bot.matching.kit_builder import replacement_note

    spec = ProductSpec(appliance=ApplianceType.HOOD,
                       raw_text="вытяжка отдельная или скошенная или труба",
                       install_type="отдельная или скошенная или труба")
    product = CatalogProduct(title="Кухонная вытяжка наклонная Midea", url="u",
                             price=18990, attributes={"Тип кухонной вытяжки": "Наклонная"})
    assert replacement_note(spec, product) == ""


# ── Приоритет «одна коллекция», иначе — сочетание по цвету ───────────────────

def _pc(brand, kind, price, color="чёрный", collection=None, in_stock=True):
    """Товар с коллекцией в характеристиках (как кладёт краулер)."""
    attrs = {"Цвет": color.capitalize()}
    if collection:
        attrs["Коллекция"] = collection
    return CatalogProduct(title=f"{kind} {brand} MODEL {color}",
                          url=f"u/{brand}/{kind}/{price}/{collection}", price=price,
                          in_stock=in_stock, attributes=attrs)


def test_kit_prefers_single_collection_over_cheaper_mix():
    # Из линейки Spectrum комплект дороже, чем «сборная солянка» того же цвета —
    # но правило заказчика: коллекция выше цены (при равном покрытии).
    specs = [_spec(OVEN), _spec(COOKTOP), _spec(HOOD)]
    cands = [
        [_pc("Korting", "Духовой шкаф", 40000, collection="Spectrum"),
         _pc("Korting", "Духовой шкаф", 30000, collection="GNBX")],
        [_pc("Korting", "Варочная панель", 25000, collection="Spectrum"),
         _pc("Korting", "Варочная панель", 18000)],                # без коллекции
        [_pc("Korting", "Вытяжка", 15000, collection="Spectrum"),
         _pc("Korting", "Вытяжка", 9000, collection="Selection")],
    ]
    kit = build_brand_kit("Korting", specs, cands, [])
    assert kit.coverage == 3
    assert kit.collection == "Spectrum"
    chosen = {m.product.attributes.get("Коллекция") for m in kit.selection.matched}
    assert chosen == {"Spectrum"}
    assert "Коллекция Korting Spectrum" in kit.design_note


def test_collection_never_beats_full_coverage():
    # Из коллекции закрывается только 2 позиции из 3, а по цвету — все 3.
    # Полнота комплекта важнее коллекции.
    specs = [_spec(OVEN), _spec(COOKTOP), _spec(HOOD)]
    cands = [
        [_pc("Korting", "Духовой шкаф", 40000, collection="Spectrum")],
        [_pc("Korting", "Варочная панель", 25000, collection="Spectrum")],
        [_pc("Korting", "Вытяжка", 15000, collection="Crystal")],   # другой линейки
    ]
    kit = build_brand_kit("Korting", specs, cands, [])
    assert kit.coverage == 3


def test_single_item_collection_is_not_a_collection():
    # Коллекция засчитывается только при ≥2 позициях из неё.
    specs = [_spec(OVEN), _spec(COOKTOP)]
    cands = [
        [_pc("Korting", "Духовой шкаф", 40000, collection="Spectrum")],
        [_pc("Korting", "Варочная панель", 25000)],
    ]
    kit = build_brand_kit("Korting", specs, cands, [])
    assert kit.coverage == 2
    assert kit.collection == ""
    assert "Коллекция" not in kit.design_note


def test_collectionless_items_are_neutral_and_noted():
    # Две позиции из линейки + одна без коллекции в той же гамме: коллекция
    # засчитана, пометка честно говорит «2 из 3».
    specs = [_spec(OVEN), _spec(COOKTOP), _spec(HOOD)]
    cands = [
        [_pc("Korting", "Духовой шкаф", 40000, collection="Spectrum")],
        [_pc("Korting", "Варочная панель", 25000, collection="Spectrum")],
        [_pc("Korting", "Вытяжка", 15000)],
    ]
    kit = build_brand_kit("Korting", specs, cands, [])
    assert kit.coverage == 3 and kit.collection == "Spectrum"
    assert "2 из 3" in kit.design_note


def test_falls_back_to_color_when_no_collection_data():
    # Ни у кого нет коллекции (Midea) — прежняя логика по цвету без изменений.
    specs = [_spec(OVEN), _spec(COOKTOP)]
    cands = [
        [_pc("Midea", "Духовой шкаф", 30000, color="белый"),
         _pc("Midea", "Духовой шкаф", 28000, color="чёрный")],
        [_pc("Midea", "Варочная панель", 20000, color="белый")],
    ]
    kit = build_brand_kit("Midea", specs, cands, [])
    assert kit.coverage == 2 and kit.collection == ""
    colors = {m.product.attributes["Цвет"] for m in kit.selection.matched}
    assert colors == {"Белый"}                     # собрались в одну гамму


def test_collection_ranks_above_design_in_rank_key():
    from max_ekrom_bot.matching.combo_pricing import compute_combo
    from max_ekrom_bot.matching.kit_builder import BrandKit
    from max_ekrom_bot.models import SelectionResult

    base = dict(brand="Korting", selection=SelectionResult(items=[]),
                combo=compute_combo([], None), requested=3, coverage=3)
    with_coll = BrandKit(**base, design_consistent=False, collection="Spectrum")
    colored = BrandKit(**base, design_consistent=True)
    assert with_coll.rank_key > colored.rank_key


def test_brand_match_is_word_bounded():
    # «lex» входит в «FLEX»: вытяжка Evelux FLEX 60 попадала в комплект Lex,
    # пылесос Haier FLEXI — в «коллекцию Lex». Сравниваем по границам слова.
    from max_ekrom_bot.matching.kit_builder import _brand_of

    assert _brand_of(CatalogProduct(title="Кухонная вытяжка Lex HUBBLE 600", url="a", price=1), "Lex")
    assert _brand_of(CatalogProduct(title="Вытяжка LEX SIMPLE 600 INOX", url="b", price=1), "Lex")
    assert _brand_of(CatalogProduct(title="Посудомойка Lex, встраиваемая", url="c", price=1), "Lex")
    assert not _brand_of(CatalogProduct(title="Кухонная вытяжка Evelux FLEX 60 BG", url="d", price=1), "Lex")
    assert not _brand_of(CatalogProduct(title="Пылесос Haier C3 FLEXI", url="e", price=1), "Lex")
    assert not _brand_of(CatalogProduct(title="Смеситель Gerdamix FLEX WHITE", url="f", price=1), "Lex")
    # поле brand тоже учитывается
    assert _brand_of(CatalogProduct(title="Духовка X", url="g", price=1, brand="Lex"), "Lex")
