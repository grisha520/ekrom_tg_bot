"""Тесты чистых хелперов пайплайна и клиента ekrom (без сети)."""

from __future__ import annotations

from max_ekrom_bot.ekrom.client import _parse_price, product_slug
from max_ekrom_bot.pipeline import (
    _model_tokens,
    _title_has_brand,
    _title_matches_model,
)


def test_model_tokens_splits_and_drops_noise():
    assert _model_tokens("OGG 771 CFX") == ["ogg", "771", "cfx"]


def test_model_tokens_keeps_single_digit_but_drops_single_letter():
    assert _model_tokens("X 7") == ["7"]
    assert _model_tokens(None) == []


def test_title_matches_model_requires_all_tokens():
    toks = _model_tokens("OGG 771 CFX")
    assert _title_matches_model("Korting OGG 771 CFX нерж", toks)
    assert not _title_matches_model("Korting OGG 771", toks)


def test_title_matches_model_empty_tokens_false():
    assert not _title_matches_model("любой", [])


def test_title_has_brand():
    assert _title_has_brand("Korting OGG 771", "korting")
    assert not _title_has_brand("Bosch HBF", "korting")
    assert not _title_has_brand("any", "")


def test_product_slug_extracts_tail():
    assert product_slug("https://www.ekrom.ru/catalog/x/products/abc-123/") == "abc-123"
    assert product_slug("/products/foo/?utm=1") == "foo"
    assert product_slug("/products/bar/#frag") == "bar"


def test_parse_price_strips_non_digits():
    assert _parse_price("45 990 ₽") == 45990.0
    assert _parse_price("") is None
    assert _parse_price("—") is None


def test_burners_query_prefers_smart_filter_over_cache():
    """При заданных конфорках кандидаты берутся из смарт-фильтра, не из кэша."""
    import asyncio

    from max_ekrom_bot.config import Settings
    from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec
    from max_ekrom_bot.pipeline import Pipeline

    class _FakeCache:
        def category_age(self, key):
            return 1.0

        def query_category(self, key):
            return [CatalogProduct(title="ИЗ КЭША", url="c")]

    class _FakeEkrom:
        def __init__(self):
            self.browsed = []

        async def browse_url(self, url, page=None):
            self.browsed.append(url)
            return [CatalogProduct(title="ИЗ СМАРТ-ФИЛЬТРА", url="s", price=100)]

        async def browse_category(self, path, page=None):
            return []

        async def search(self, q, page=None):
            return []

    p = Pipeline(Settings(LLM_PROVIDER="rule", USE_CATALOG_CACHE="true"))
    p._cache = _FakeCache()
    ek = _FakeEkrom()
    spec = ProductSpec(appliance=ApplianceType.COOKTOP, fuel_type="индукционная", burners=2)
    cands = asyncio.run(p._candidates_for_spec(ek, spec))
    assert [c.title for c in cands] == ["ИЗ СМАРТ-ФИЛЬТРА"]
    assert ek.browsed and "arrFilter_1762" in ek.browsed[0]  # фильтр по конфоркам


def test_no_burners_uses_cache():
    """Без конфорок — кандидаты из кэша (смарт-фильтр не трогаем)."""
    import asyncio

    from max_ekrom_bot.config import Settings
    from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec
    from max_ekrom_bot.pipeline import Pipeline

    class _FakeCache:
        def category_age(self, key):
            return 1.0

        def query_category(self, key):
            return [CatalogProduct(title="ИЗ КЭША", url="c", price=100)]

    class _FakeEkrom:
        async def browse_url(self, url, page=None):
            raise AssertionError("не должны ходить в браузер при живом кэше")

    p = Pipeline(Settings(LLM_PROVIDER="rule", USE_CATALOG_CACHE="true"))
    p._cache = _FakeCache()
    spec = ProductSpec(appliance=ApplianceType.COOKTOP, fuel_type="индукционная")
    cands = asyncio.run(p._candidates_for_spec(_FakeEkrom(), spec))
    assert [c.title for c in cands] == ["ИЗ КЭША"]
