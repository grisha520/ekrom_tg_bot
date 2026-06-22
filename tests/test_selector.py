"""Тесты детерминированного подбора (ProductSelector)."""

from __future__ import annotations

from max_ekrom_bot.matching.selector import ProductSelector, _title_has_color
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
