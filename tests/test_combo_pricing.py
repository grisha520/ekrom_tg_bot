"""Тесты движка расчёта итога комплекта (Ф2 проекта комплектов)."""

from __future__ import annotations

from max_ekrom_bot.matching.combo_pricing import ComboItem, compute_combo


class _Camp:
    """Лёгкая заглушка кампании (как PromoCampaign / строка PromoStore)."""

    def __init__(self, mechanic, tiers=None, requires_oven=False):
        self.mechanic = mechanic
        self.tiers = tiers or {}
        self.requires_oven = requires_oven


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
