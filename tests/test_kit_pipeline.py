"""Интеграция движка комплекта в pipeline (без браузера) — Ф4.

Проверяем чистую часть `Pipeline._kit_from_candidates` и `build_kit_pdf` на
временных PromoStore/CatalogCache: выбор бренда, оцифровка подарка из каталога,
генерация PDF комплекта.
"""

from __future__ import annotations

import pytest

pytest.importorskip("reportlab")

from pypdf import PdfReader  # noqa: E402

from max_ekrom_bot.config import Settings  # noqa: E402
from max_ekrom_bot.ekrom.catalog_cache import CatalogCache  # noqa: E402
from max_ekrom_bot.ekrom.promo_store import PromoStore  # noqa: E402
from max_ekrom_bot.ekrom.promos import GiftRule, PromoCampaign  # noqa: E402
from max_ekrom_bot.models import (  # noqa: E402
    ApplianceType, CatalogProduct, CustomerRequest, MatchedItem, ProductSpec,
    SelectionResult,
)
from max_ekrom_bot.pipeline import Pipeline  # noqa: E402

OVEN, COOKTOP, HOOD = ApplianceType.OVEN, ApplianceType.COOKTOP, ApplianceType.HOOD


def _pipeline():
    return Pipeline(Settings(LLM_PROVIDER="rule", MANAGER_NAME="Иван"))


def _req(*appliances):
    return CustomerRequest(specs=[ProductSpec(appliance=a, raw_text=a.value) for a in appliances])


def _p(brand, kind, price, color="чёрный"):
    return CatalogProduct(title=f"{kind} {brand} MODEL {color}", url=f"u/{brand}/{kind}",
                          price=price)


def _camp(**kw):
    base = dict(slug="s", url="u", title="t", brand="Korting")
    base.update(kw)
    return PromoCampaign(**base)


def test_kit_from_candidates_picks_brand_with_cascade(tmp_path):
    store = PromoStore(tmp_path / "c.db")
    store.replace_all([_camp(slug="casc", mechanic="cascade_cheapest",
                             tiers={2: 25, 3: 50, 4: 75, 5: 100}, requires_oven=True)])
    cache = CatalogCache(tmp_path / "cat.db")

    cands = [
        [_p("Korting", "Духовой шкаф", 40000), _p("Midea", "Духовой шкаф", 35000)],
        [_p("Korting", "Варочная панель", 25000)],
        [_p("Korting", "Вытяжка", 15000)],
    ]
    kit = _pipeline()._kit_from_candidates(_req(OVEN, COOKTOP, HOOD), cands,
                                           store=store, cache=cache)
    assert kit is not None and kit.brand == "Korting" and kit.is_full
    assert kit.combo.mechanic == "cascade_cheapest"
    assert kit.combo.discount_total == 7500.0  # 50% на наименьший (вытяжка 15000)


def test_kit_gift_valued_from_catalog(tmp_path):
    store = PromoStore(tmp_path / "c.db")
    store.replace_all([_camp(slug="mbt", mechanic="gift",
                             gift=GiftRule("sum", {10000: "KWK 0902"}))])
    cache = CatalogCache(tmp_path / "cat.db")
    cache.upsert_many([CatalogProduct(title="Чайник Korting KWK 0902 G", url="g", price=3990)],
                      "gifts")

    cands = [[_p("Korting", "Духовой шкаф", 40000)], [_p("Korting", "Варочная панель", 20000)]]
    kit = _pipeline()._kit_from_candidates(_req(OVEN, COOKTOP), cands, store=store, cache=cache)
    assert kit.brand == "Korting"
    assert kit.combo.gift_title == "Чайник Korting KWK 0902 G"
    assert kit.combo.discount_total == 3990.0
    assert kit.combo.final_total == 60000.0  # подарок сумму не уменьшает


def test_kit_lex_without_promo(tmp_path):
    store = PromoStore(tmp_path / "c.db")  # без кампаний
    cache = CatalogCache(tmp_path / "cat.db")
    cands = [[_p("Lex", "Духовой шкаф", 30000)], [_p("Lex", "Варочная панель", 18000)]]
    kit = _pipeline()._kit_from_candidates(_req(OVEN, COOKTOP), cands, store=store, cache=cache,
                                           level="budget")
    assert kit.brand == "Lex" and kit.combo.applied is False
    assert kit.combo.final_total == 48000.0


def test_kit_out_of_scope_positions_go_to_manager(tmp_path):
    # Мойка/смеситель — вне охвата 5 брендов: уходят менеджеру, комплект собирается
    # из остальных позиций.
    store = PromoStore(tmp_path / "c.db")
    cache = CatalogCache(tmp_path / "cat.db")
    req = CustomerRequest(specs=[
        ProductSpec(appliance=OVEN, raw_text="духовой шкаф"),
        ProductSpec(appliance=COOKTOP, raw_text="варочная панель"),
        ProductSpec(appliance=ApplianceType.UNKNOWN, raw_text="мойка нержавейка накладная"),
    ])
    cands = [
        [_p("Korting", "Духовой шкаф", 40000)],
        [_p("Korting", "Варочная панель", 25000)],
        [CatalogProduct(title="Мойка EMAR EMB-131", url="m", price=19935)],
    ]
    kit = _pipeline()._kit_from_candidates(req, cands, store=store, cache=cache)
    assert kit is not None and kit.brand == "Korting"
    assert kit.coverage == 2 and kit.requested == 2      # мойка не в счёте покрытия
    assert [s.raw_text for s in kit.manager_specs] == ["мойка нержавейка накладная"]


def test_kit_whole_request_out_of_scope(tmp_path):
    # Весь запрос вне охвата (сантехника) → «пустой» комплект с manager_specs.
    store = PromoStore(tmp_path / "c.db")
    cache = CatalogCache(tmp_path / "cat.db")
    req = CustomerRequest(specs=[
        ProductSpec(appliance=ApplianceType.UNKNOWN, raw_text="мойка графит 700"),
        ProductSpec(appliance=ApplianceType.UNKNOWN, raw_text="смеситель с фильтром"),
    ])
    kit = _pipeline()._kit_from_candidates(req, [[], []], store=store, cache=cache)
    assert kit is not None and kit.coverage == 0
    assert len(kit.manager_specs) == 2


def test_kit_respects_client_brand_mentions(tmp_path):
    # «можно Maunfeld» в сообщении → Korting не рассматриваем, даже если он выгоднее.
    store = PromoStore(tmp_path / "c.db")
    store.replace_all([_camp(slug="casc", brand="Korting", mechanic="cascade_cheapest",
                             tiers={2: 25}, requires_oven=True)])
    cache = CatalogCache(tmp_path / "cat.db")
    req = CustomerRequest(
        specs=[ProductSpec(appliance=OVEN, raw_text="духовой шкаф"),
               ProductSpec(appliance=COOKTOP, raw_text="варочная панель")],
        original_message="можно Maunfeld: духовой шкаф и варочная панель",
    )
    cands = [
        [_p("Korting", "Духовой шкаф", 40000), _p("Maunfeld", "Духовой шкаф", 42000)],
        [_p("Korting", "Варочная панель", 20000), _p("Maunfeld", "Варочная панель", 21000)],
    ]
    kit = _pipeline()._kit_from_candidates(req, cands, store=store, cache=cache)
    assert kit.brand == "Maunfeld"


def test_build_kit_pdf(tmp_path):
    store = PromoStore(tmp_path / "c.db")
    store.replace_all([_camp(slug="casc", mechanic="cascade_cheapest",
                             tiers={2: 25, 3: 50}, requires_oven=True)])
    cache = CatalogCache(tmp_path / "cat.db")
    cands = [
        [_p("Korting", "Духовой шкаф", 40000)],
        [_p("Korting", "Варочная панель", 20000)],
    ]
    pipe = _pipeline()
    kit = pipe._kit_from_candidates(_req(OVEN, COOKTOP), cands, store=store, cache=cache)
    out = pipe.build_kit_pdf(kit, tmp_path / "kit.pdf")
    assert out.exists() and out.stat().st_size > 1000
    text = "".join(p.extract_text() or "" for p in PdfReader(str(out)).pages)
    assert "Итого" in text and "Индивидуальная скидка" in text
    # Каскад −25% на наименьший (20000→15000): в карточке новая цена и «Скидка -25%».
    assert "Скидка -25%" in text and "15 000 ₽" in text


# ── _kit_render_selection: per-item скидки к копиям (не наслаивает) ───────────

def test_kit_render_selection_applies_item_discount_on_copies():
    from max_ekrom_bot.pipeline import _kit_render_selection
    from max_ekrom_bot.matching.kit_builder import BrandKit
    from max_ekrom_bot.matching.combo_pricing import ComboResult

    sel = SelectionResult(items=[
        MatchedItem(spec=ProductSpec(appliance=OVEN),
                    product=CatalogProduct(title="Духовой", url="u", price=40000)),
        MatchedItem(spec=ProductSpec(appliance=ApplianceType.FRIDGE),
                    product=CatalogProduct(title="Холодильник", url="f", price=100000)),
    ])
    combo = ComboResult(mechanic="spec_price", subtotal=140000, discount_total=73000,
                        final_total=67000, applied=True, item_discounts={1: 73.0})
    kit = BrandKit(brand="X", selection=sel, combo=combo, requested=2, coverage=2,
                   design_consistent=True)

    r1 = _kit_render_selection(kit)
    fridge = r1.items[1].product
    assert fridge.old_price == 100000 and fridge.price == 27000 and fridge.discount_pct == 73.0
    assert r1.items[0].product.price == 40000            # позиция без скидки не тронута
    # оригинальный selection не изменён (копии), повторный вызов не наслаивает
    assert sel.items[1].product.price == 100000
    r2 = _kit_render_selection(kit)
    assert r2.items[1].product.price == 27000


def test_kit_render_selection_keeps_unmatched():
    from max_ekrom_bot.pipeline import _kit_render_selection
    from max_ekrom_bot.matching.kit_builder import BrandKit
    from max_ekrom_bot.matching.combo_pricing import ComboResult

    sel = SelectionResult(items=[
        MatchedItem(spec=ProductSpec(appliance=OVEN),
                    product=CatalogProduct(title="Духовой", url="u", price=40000)),
        MatchedItem(spec=ProductSpec(appliance=COOKTOP, raw_text="варка"), product=None),
    ])
    combo = ComboResult(mechanic="none", subtotal=40000, discount_total=0, final_total=40000)
    kit = BrandKit(brand="X", selection=sel, combo=combo, requested=2, coverage=1,
                   design_consistent=True)
    r = _kit_render_selection(kit)
    assert len(r.items) == 2 and r.items[1].product is None   # ненайденная сохранена
