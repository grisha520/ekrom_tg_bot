"""Тесты движка расчёта итога комплекта (Ф2 проекта комплектов)."""

from __future__ import annotations

from max_ekrom_bot.matching.combo_pricing import (
    ComboItem, best_combo, compute_combo, make_gift_valuer,
)


class _Camp:
    """Лёгкая заглушка кампании (как PromoCampaign / строка PromoStore)."""

    def __init__(self, mechanic, tiers=None, requires_oven=False, slug=None, gift=None):
        self.mechanic = mechanic
        self.tiers = tiers or {}
        self.requires_oven = requires_oven
        self.slug = slug or mechanic
        self.gift = gift  # {"condition": "sum"|"count", "options": {порог: модель}}


def _oven(price):
    return ComboItem("Духовой шкаф", price, "oven", is_oven=True)


def _cooktop(price):
    return ComboItem("Варочная панель", price, "cooktop")


def _hood(price):
    return ComboItem("Вытяжка", price, "hood")


KORTING_CASCADE = _Camp("cascade_cheapest", {2: 25, 3: 50, 4: 75, 5: 100}, requires_oven=True)
MAUNFELD_FLAT = _Camp("flat_combo", {3: 8, 4: 11, 5: 13, 6: 15})
KUP_FLAT = _Camp("flat_combo", {2: 5, 3: 7, 4: 10, 5: 12})


def test_cascade_discount_on_cheapest():
    # Живой эталон: духовка 45990 + панель 25490, −25% на наименьший (панель).
    items = [_oven(45990), _cooktop(25490)]
    r = compute_combo(items, KORTING_CASCADE)
    assert r.applied is True
    assert r.mechanic == "cascade_cheapest"
    assert r.item_discounts == {1: 25}          # скидка на индекс 1 (панель — дешевле)
    assert r.discount_total == 6372.5
    assert r.final_total == 65107.5
    assert r.subtotal == 71480.0


def test_cascade_five_items_cheapest_free():
    items = [_oven(50000), _cooktop(30000), _hood(20000),
             ComboItem("ПММ", 40000, "dishwasher"), ComboItem("СВЧ", 15000, "microwave")]
    r = compute_combo(items, KORTING_CASCADE)
    assert r.item_discounts == {4: 100}         # 5 позиций → −100% на наименьший (15000)
    assert r.discount_total == 15000.0
    assert r.final_total == 140000.0


def test_cascade_requires_oven():
    # Нет духовки → скидка не применяется, честная пометка.
    items = [_cooktop(25490), _hood(13690)]
    r = compute_combo(items, KORTING_CASCADE)
    assert r.applied is False
    assert r.discount_total == 0.0
    assert r.final_total == r.subtotal
    assert "духов" in r.note.lower()


def test_flat_combo_maunfeld():
    items = [_oven(30000), _cooktop(20000), _hood(10000)]  # 3 позиции → 8%
    r = compute_combo(items, MAUNFELD_FLAT)
    assert r.applied is True
    assert r.combo_discount_pct == 8
    assert r.discount_total == 4800.0            # 8% от 60000
    assert r.final_total == 55200.0


def test_flat_combo_below_min_count():
    # Maunfeld начинается с 3 позиций; для 2 — скидки нет.
    items = [_oven(30000), _cooktop(20000)]
    r = compute_combo(items, MAUNFELD_FLAT)
    assert r.applied is False
    assert r.final_total == r.subtotal
    assert "от 3" in r.note


def test_flat_combo_caps_at_top_tier():
    # 7 позиций при сетке до 5 (Kuppersberg) → берём верхний тариф 12%.
    items = [ComboItem(f"t{i}", 10000, f"cat{i}") for i in range(7)]
    r = compute_combo(items, KUP_FLAT)
    assert r.combo_discount_pct == 12
    assert r.discount_total == 8400.0            # 12% от 70000


def test_distinct_categories_drive_count():
    # Две позиции ОДНОЙ категории → distinct=1 → ниже минимума flat (2) → нет скидки.
    items = [_hood(10000), _hood(12000)]
    r = compute_combo(items, KUP_FLAT)
    assert r.applied is False


def test_gift_is_manager_review():
    r = compute_combo([_oven(40000), _cooktop(20000), _hood(10000)], _Camp("gift"))
    assert r.manager_review is True
    assert r.applied is False
    assert r.final_total == r.subtotal
    assert "менеджер" in r.note.lower()


def test_unknown_is_manager_review():
    r = compute_combo([_oven(40000)], _Camp("unknown"))
    assert r.manager_review is True


def test_no_campaign_lex_no_discount():
    # Lex: акции нет → цена по прайсу, без скидки, без пометки менеджера.
    items = [_oven(40000), _cooktop(20000)]
    r = compute_combo(items, None)
    assert r.mechanic == "none"
    assert r.applied is False
    assert r.manager_review is False
    assert r.final_total == 60000.0


def test_empty_items():
    r = compute_combo([], KORTING_CASCADE)
    assert r.subtotal == 0.0 and r.final_total == 0.0 and r.applied is False


def test_accepts_dict_campaign_from_store():
    # PromoStore.campaigns_for_brand отдаёт строки-словари — движок их принимает.
    camp = {"mechanic": "flat_combo", "tiers": {2: 5, 3: 7}, "requires_oven": False}
    r = compute_combo([_oven(30000), _cooktop(20000)], camp)
    assert r.combo_discount_pct == 5
    assert r.discount_total == 2500.0


# ── Подарок как выгода + выбор выгоднейшей акции ──────────────────────────────

def _gift_value_5000(campaign, items):
    # Оцифровщик-заглушка: подарок «Чайник» стоимостью 5000, если ≥2 позиций.
    return ("Чайник KWK 0902 G", 5000) if len(items) >= 2 else None


def test_gift_valued_does_not_reduce_total():
    items = [_oven(30000), _cooktop(20000)]
    r = compute_combo(items, _Camp("gift"), gift_value=_gift_value_5000)
    assert r.applied is True
    assert r.manager_review is False
    assert r.gift_title == "Чайник KWK 0902 G"
    assert r.discount_total == 5000.0        # выгода = цена подарка
    assert r.final_total == 50000.0          # сумма НЕ уменьшается — подарок сверху


def test_gift_without_valuer_is_manager_review():
    r = compute_combo([_oven(30000), _cooktop(20000)], _Camp("gift"))
    assert r.manager_review is True and r.applied is False


def test_best_combo_picks_highest_benefit():
    # У бренда две акции: каскад −25% на наименьший (20000→5000) и подарок на 8000.
    items = [_oven(40000), _cooktop(20000)]
    cascade = _Camp("cascade_cheapest", {2: 25}, slug="cascade")
    gift = _Camp("gift", slug="gift")

    def gift_8000(c, it):
        return ("СВЧ в подарок", 8000)

    best = best_combo(items, [cascade, gift], gift_value=gift_8000)
    assert best.campaign_slug == "gift"      # подарок 8000 > скидка 5000
    assert best.discount_total == 8000.0


def test_best_combo_prefers_bigger_discount():
    items = [_oven(40000), _cooktop(20000)]
    cascade = _Camp("cascade_cheapest", {2: 25}, slug="cascade")  # 5000
    gift = _Camp("gift", slug="gift")

    def gift_3000(c, it):
        return ("Мелочь", 3000)

    best = best_combo(items, [cascade, gift], gift_value=gift_3000)
    assert best.campaign_slug == "cascade"   # скидка 5000 > подарок 3000
    assert best.discount_total == 5000.0


def test_best_combo_falls_back_to_manager_review():
    # Ни одна не считается численно (подарок без оцифровщика) → пометка менеджеру.
    items = [_oven(40000), _cooktop(20000)]
    best = best_combo(items, [_Camp("gift"), _Camp("unknown")])
    assert best.manager_review is True


def test_best_combo_no_campaigns_is_price_list():
    # Lex: акций нет → по прайсу.
    best = best_combo([_oven(40000), _cooktop(20000)], [])
    assert best.mechanic == "none" and best.applied is False
    assert best.final_total == 60000.0


# ── make_gift_valuer: оцифровка подарка из условия + каталога ─────────────────

_CATALOG = {
    "KWK 0902": ("Чайник Korting KWK 0902 G", 3990),
    "KHB 0315": ("Блендер Korting KHB 0315 W Tulip", 5490),
    "KCM 1005": ("Кофеварка Korting KCM 1005 T Arte", 8990),
    "MID45": ("Посудомоечная машина Midea MID45S100I", 27990),
}
_lookup = _CATALOG.get


def test_gift_valuer_sum_picks_highest_reached_threshold():
    valuer = make_gift_valuer(_lookup)
    gift = {"condition": "sum", "options": {10000: "KWK 0902", 25000: "KHB 0315", 50000: "KCM 1005"}}
    camp = _Camp("gift", gift=gift, slug="korting_mbt")
    # Сумма 60000 ≥ 50000 → подарок наибольшего достигнутого порога (кофеварка).
    r = compute_combo([_oven(40000), _cooktop(20000)], camp, gift_value=valuer)
    assert r.applied and r.gift_title == "Кофеварка Korting KCM 1005 T Arte"
    assert r.discount_total == 8990.0 and r.final_total == 60000.0


def test_gift_valuer_sum_below_threshold_none():
    valuer = make_gift_valuer(_lookup)
    gift = {"condition": "sum", "options": {50000: "KCM 1005"}}
    r = compute_combo([_cooktop(20000), _hood(15000)], _Camp("gift", gift=gift), gift_value=valuer)
    assert r.manager_review is True and r.applied is False  # 35000 < 50000


def test_gift_valuer_count_requires_oven():
    valuer = make_gift_valuer(_lookup)
    gift = {"condition": "count", "options": {4: "MID45"}}
    camp = _Camp("gift", requires_oven=True, gift=gift, slug="midea_pmm")
    four = [_oven(40000), _cooktop(20000), _hood(15000), ComboItem("ПММ", 30000, "dishwasher")]
    r = compute_combo(four, camp, gift_value=valuer)
    assert r.applied and r.discount_total == 27990.0
    # Тот же набор без духовки — подарок не даётся.
    no_oven = [_cooktop(20000), _hood(15000), ComboItem("ПММ", 30000, "dishwasher"),
               ComboItem("СВЧ", 12000, "microwave")]
    r2 = compute_combo(no_oven, camp, gift_value=valuer)
    assert r2.applied is False and r2.manager_review is True


def test_gift_valuer_model_not_in_catalog_none():
    valuer = make_gift_valuer(_lookup)
    gift = {"condition": "sum", "options": {10000: "UNKNOWN 999"}}
    r = compute_combo([_oven(40000)], _Camp("gift", gift=gift), gift_value=valuer)
    assert r.manager_review is True


# ── Спеццена холодильника + подарок-вычет + исключения (эталоны менеджера) ────

KUP_SPEC = {  # kuppersberg_kholodilnik_po_spets_tsene (условия с сайта)
    "mechanic": "spec_price", "requires_oven": True, "slug": "kup_spec",
    "tiers": {}, "gift": None,
    "spec": {"models": ["RBN 1761", "RBN 1965"], "tiers": {3: 40000.0, 4: 27000.0},
             "pct": None, "line_label": None},
    "exclusions": ["домино", "отдельностоящая", "соло-свч", "измельчитель"],
}


def _fridge(price=97281):
    return ComboItem("Встраиваемый холодильник Kuppersberg RBN 1965", price, "fridge",
                     install="встраиваемый")


def test_spec_price_four_items_fixed_27000():
    # Эталон ИП Хорошев: комплект из 4+ предметов → холодильник за фикс-цену (ниж. порог 27 000).
    items = [_oven(45000), _cooktop(25000), _hood(15000),
             ComboItem("ПММ", 40000, "dishwasher"), _fridge(97281)]
    r = compute_combo(items, KUP_SPEC)
    assert r.applied and r.mechanic == "spec_price"
    assert r.discount_total == 97281 - 27000            # выгода = РРЦ − фикс
    assert r.final_total == r.subtotal - r.discount_total
    assert 4 in r.item_discounts or list(r.item_discounts) == [4]  # скидка на холодильник


def test_spec_price_three_items_fixed_40000():
    items = [_oven(45000), _cooktop(25000), _hood(15000), _fridge(97281)]
    r = compute_combo(items, KUP_SPEC)
    assert r.applied and r.discount_total == 97281 - 40000


def test_spec_price_requires_special_model():
    # В комплекте холодильник НЕ спец-модели → акция не применяется, честная пометка.
    items = [_oven(45000), _cooktop(25000), _hood(15000),
             ComboItem("Встраиваемый холодильник Kuppersberg RBN 1765", 80396, "fridge")]
    r = compute_combo(items, KUP_SPEC)
    assert r.applied is False and "RBN 1761" in r.note


def test_spec_price_domino_not_counted():
    # Домино не участвует: 2 обычных предмета + домино → порога 3 нет.
    items = [_oven(45000),
             ComboItem("Варочная панель домино", 12000, "cooktop", width_mm=288),
             _hood(15000), _fridge(97281)]
    r = compute_combo(items, KUP_SPEC)
    assert r.applied is False and "от 3" in r.note


def test_spec_price_korting_exclusive_line_manager_review():
    camp = {"mechanic": "spec_price", "requires_oven": False, "slug": "kor50",
            "tiers": {}, "gift": None, "exclusions": ["мбт"],
            "spec": {"models": ["KSI 19547"], "tiers": {}, "pct": 50.0,
                     "line_label": "Эксклюзив"}}
    items = [_oven(50000), ComboItem("Встраиваемый холодильник Korting KSI 19547 CFNFZ",
                                     94990, "fridge")]
    r = compute_combo(items, camp)
    assert r.manager_review is True and "Эксклюзив" in r.note


def test_gift_inside_kit_deducted():
    # Эталон Stefany: подарочная ПММ — одна из запрошенных позиций → −100% и ВЫЧЕТ.
    camp = _Camp("gift", gift={"condition": "count", "options": {4: "MID45"}},
                 requires_oven=True, slug="midea_pmm")
    items = [_oven(40000), _cooktop(20000), _hood(15000),
             ComboItem("Морозильник", 30000, "freezer"),
             ComboItem("Посудомоечная машина Midea MID45S100i", 27990, "dishwasher")]
    valuer = make_gift_valuer(lambda m: ("Посудомоечная машина Midea MID45S100i", 27990))
    r = compute_combo(items, camp, gift_value=valuer)
    assert r.applied is True
    assert r.item_discounts == {4: 100.0}               # ПММ обнулена
    assert r.discount_total == 27990.0
    assert r.final_total == r.subtotal - 27990.0        # подарок ВЫЧТЕН из итога


def test_flat_combo_excludes_domino_from_base():
    # Эталон Be-Art (Maunfeld): −11% на 4 акционные позиции, домино вне базы и счёта.
    camp = _Camp("flat_combo", {3: 8, 4: 11, 5: 13}, slug="maunfeld")
    camp.exclusions = ["домино", "соло-свч"]
    items = [ComboItem("Стиральная машина", 61059, "washer"),
             ComboItem("Духовой с СВЧ", 67660, "oven", is_oven=True),
             ComboItem("Холодильник", 100990, "fridge"),
             ComboItem("ПММ", 40390, "dishwasher"),
             ComboItem("Варочная панель Maunfeld CVI292BK", 11100, "cooktop", width_mm=290)]
    r = compute_combo(items, camp)
    assert r.applied and r.combo_discount_pct == 11     # 4 участвующих → 11%
    base = 61059 + 67660 + 100990 + 40390               # без домино
    assert r.discount_total == round(base * 0.11, 2)    # 29 710.89 ≈ эталон 29 711
    assert r.final_total == round(r.subtotal - r.discount_total, 2)


def test_cascade_cheapest_skips_excluded():
    # Каскад: наименьший выбирается среди УЧАСТВУЮЩИХ (домино пропускается).
    camp = _Camp("cascade_cheapest", {2: 25}, slug="casc")
    camp.exclusions = ["домино"]
    items = [_oven(40000), _cooktop(20000),
             ComboItem("Варочная домино", 9000, "cooktop2", width_mm=288)]
    r = compute_combo(items, camp)
    assert r.item_discounts == {1: 25}                  # скидка на 20000, не на домино
    assert r.discount_total == 5000.0


def test_best_combo_with_real_gift_valuer_vs_discount():
    # Бренд: каскад −25% на наименьший (20000→5000) vs подарок-кофеварка 8990. Подарок выгоднее.
    valuer = make_gift_valuer(_lookup)
    items = [_oven(40000), _cooktop(20000)]
    cascade = _Camp("cascade_cheapest", {2: 25}, slug="cascade")
    gift = _Camp("gift", gift={"condition": "sum", "options": {50000: "KCM 1005"}}, slug="mbt")
    best = best_combo(items, [cascade, gift], gift_value=valuer)
    assert best.campaign_slug == "mbt" and best.discount_total == 8990.0
