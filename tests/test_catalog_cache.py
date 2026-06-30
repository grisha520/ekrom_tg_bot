"""Тесты SQLite-кэша каталога."""

from __future__ import annotations

from max_ekrom_bot.ekrom.catalog_cache import CatalogCache
from max_ekrom_bot.models import CatalogProduct


def _p(url, title, price, in_stock=True, attrs=None, img=None):
    return CatalogProduct(
        url=url, title=title, price=price, in_stock=in_stock,
        attributes=attrs or {}, image_url=img,
    )


CAT = "/catalog/tekhnika_dlya_kukhni/dukhovye_shkafy/"


def test_upsert_and_query(tmp_path):
    cache = CatalogCache(tmp_path / "c.db")
    n = cache.upsert_many([_p("u1", "Духовка A", 100), _p("u2", "Духовка B", 200)], CAT)
    assert n == 2
    got = cache.query_category(CAT)
    assert {p.url for p in got} == {"u1", "u2"}
    assert {p.price for p in got} == {100.0, 200.0}
    assert all(p.in_stock is True for p in got)
    assert cache.count() == 2


def test_query_empty_for_unknown_category(tmp_path):
    cache = CatalogCache(tmp_path / "c.db")
    assert cache.query_category("/nope/") == []
    assert cache.category_age("/nope/") is None


def test_category_age_recent(tmp_path):
    cache = CatalogCache(tmp_path / "c.db")
    cache.upsert_many([_p("u1", "A", 100)], CAT)
    age = cache.category_age(CAT)
    assert age is not None and age < 5  # только что записали


def test_upsert_keeps_attributes_on_relisting(tmp_path):
    # Краул перезаписывает листинг пустыми attrs — накопленные характеристики/фото
    # не должны затираться.
    cache = CatalogCache(tmp_path / "c.db")
    cache.upsert_many([_p("u1", "A", 100)], CAT)
    cache.update_enrichment("u1", {"Цвет": "Чёрный"}, "https://x/p.jpg")
    cache.upsert_many([_p("u1", "A", 120)], CAT)  # повторный краул, attrs={}
    got = cache.query_category(CAT)[0]
    assert got.price == 120.0  # цена обновилась
    assert got.attributes == {"Цвет": "Чёрный"}  # характеристики сохранились
    assert got.image_url == "https://x/p.jpg"  # фото сохранилось


def test_update_enrichment_sets_attrs_and_image(tmp_path):
    cache = CatalogCache(tmp_path / "c.db")
    cache.upsert_many([_p("u1", "A", 100)], CAT)
    cache.update_enrichment("u1", {"Объем, л": "71"}, "https://x/i.jpg")
    got = cache.query_category(CAT)[0]
    assert got.attributes == {"Объем, л": "71"}
    assert got.image_url == "https://x/i.jpg"
