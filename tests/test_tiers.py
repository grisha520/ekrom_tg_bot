"""Тесты ценовых уровней комплекта (бюджет / оптимум / премиум)."""

from __future__ import annotations

import asyncio

from max_ekrom_bot.config import Settings
from max_ekrom_bot.matching.selector import ProductSelector
from max_ekrom_bot.messenger.client_ux import TIER_ORDER, TIER_PDF_LABELS, tier_keyboard
from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec
from max_ekrom_bot.pipeline import Pipeline


def _p(title: str, price: float) -> CatalogProduct:
    return CatalogProduct(title=title, url=f"https://x/products/{price}/", price=price)


CANDS = [_p("A", 100), _p("B", 200), _p("C", 300), _p("D", 5000)]  # медиана 250


def _pick(level: str | None, segment: str):
    p = Pipeline(Settings(LLM_PROVIDER="rule", USE_CATALOG_CACHE=False))
    sel = ProductSelector(segment=segment)
    spec = ProductSpec(appliance=ApplianceType.OVEN)
    return asyncio.run(
        p._select_available(None, sel, spec, list(CANDS), None, level=level)
    )


def test_budget_level_picks_cheapest():
    item = _pick("budget", "эконом")
    assert item.product.price == 100


def test_premium_level_capped_not_outlier():
    # Кап ×3 медианы (750): выброс 5000 отсекается, премиум = 300.
    item = _pick("premium", "премиум")
    assert item.product.price == 300


def test_premium_cap_keeps_all_when_no_outliers():
    p = Pipeline(Settings(LLM_PROVIDER="rule", USE_CATALOG_CACHE=False))
    sel = ProductSelector(segment="премиум")
    spec = ProductSpec(appliance=ApplianceType.OVEN)
    cands = [_p("A", 100), _p("B", 200), _p("C", 300)]
    item = asyncio.run(p._select_available(None, sel, spec, cands, None, level="premium"))
    assert item.product.price == 300  # без выбросов — просто самый дорогой


def test_optimal_level_unchanged():
    item = _pick(None, "средний")
    assert item.product.price == 200  # медианный выбор как раньше


def test_tier_keyboard_marks_active_and_has_pdf():
    kb = tier_keyboard("tok123", "premium")
    texts = [b.text for row in kb.inline_keyboard for b in row]
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(t.startswith("✓ ") and "Премиум" in t for t in texts)
    assert not any(t.startswith("✓ ") and "Бюджет" in t for t in texts)
    assert "tierpdf:tok123" in datas
    for lvl in TIER_ORDER:
        assert f"tier:tok123:{lvl}" in datas


def test_tier_pdf_labels_complete():
    assert set(TIER_PDF_LABELS) == set(TIER_ORDER)
