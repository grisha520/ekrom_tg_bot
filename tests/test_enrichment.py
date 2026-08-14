"""Тесты атрибут-зависимых детекторов характеристик (#4).

Проверяют чтение сигналов из структурированных характеристик товара, а не
только из названия.
"""

from __future__ import annotations

from max_ekrom_bot.matching.selector import (
    ProductSelector,
    _canon_color,
    _color_conflict,
    _install_family,
    _product_burners,
    _product_colors,
    _product_fuels,
    _product_width,
    _title_width_mm,
)
from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec


def _p(title: str, attrs: dict[str, str] | None = None, price: float = 1.0) -> CatalogProduct:
    return CatalogProduct(title=title, url="u", price=price, attributes=attrs or {})


def test_color_from_attribute():
    assert "чёрный" in _product_colors(_p("Духовка LEX EDM", {"Цвет": "Чёрный"}))


def test_fuel_from_type_attr_ignores_management_field():
    # «Тип управления: Электронное» НЕ должно дать электрическую плиту,
    # а «Вид варочной поверхности: Газовая» — должно дать газовую.
    p = _p("Панель", {"Тип управления": "Электронное", "Вид варочной поверхности": "Газовая"})
    assert _product_fuels(p) == {"газовая"}


def test_burners_from_attribute():
    assert _product_burners(_p("Панель LEX", {"Количество конфорок": "4"})) == 4


def test_width_from_embedding_attribute():
    assert _product_width(_p("Духовка", {"Ширина встраивания, мм": "600"})) == 600


def test_helpers_fallback_to_title_without_attrs():
    assert _product_burners(_p("Панель на 3 конфорки")) == 3
    assert "белый" in _product_colors(_p("Духовка белая"))


def test_attribute_contradiction_dropped():
    # Цвет не виден в названии, но виден в характеристиках — противоречие ловится.
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.OVEN, color="белый")
    black = _p("Духовка LEX EDM", {"Цвет": "Чёрный"}, price=100)
    white = _p("Духовка BOSCH HBF", {"Цвет": "Белый"}, price=120)
    ranked = sel.ranked_candidates(spec, [black, white])
    assert [c.title for c in ranked] == ["Духовка BOSCH HBF"]


def test_color_family_conflict_logic():
    # Оттенки одной семьи не конфликтуют; явно другой цвет — конфликтует.
    assert _color_conflict("чёрный", {"графит"}) is False
    assert _color_conflict("чёрный", {"антрацит"}) is False
    assert _color_conflict("чёрный", {"белый"}) is True
    assert _color_conflict("белый", {"графит"}) is True
    assert _color_conflict("чёрный", set()) is False
    assert _color_conflict("чёрный", {"чёрный"}) is False


def test_graphite_accepted_white_rejected_for_black_request():
    # Под «чёрную» вытяжку: графит остаётся, белая (Цвет: Белый) отсекается.
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.HOOD, color="чёрный")
    graphite = _p("Вытяжка X", {"Цвет": "Графит"}, price=100)
    white = _p("Вытяжка Y BLANC", {"Цвет": "Белый"}, price=120)
    ranked = sel.ranked_candidates(spec, [graphite, white])
    assert [c.title for c in ranked] == ["Вытяжка X"]


def test_color_confirmed_by_title_fast_path():
    sel = ProductSelector(segment="средний")
    black = ProductSpec(appliance=ApplianceType.HOOD, color="чёрный")
    # цвет виден и совместим в названии → подтверждён (страницу открывать не надо)
    assert sel.color_confirmed_by_title(black, _p("Вытяжка Lex HOGAN 600 BLACK")) is True
    assert sel.color_confirmed_by_title(black, _p("Вытяжка X графит")) is True
    # цвет в названии не распознан (BLANC) → не подтверждён, нужна страница
    assert sel.color_confirmed_by_title(black, _p("Вытяжка Monsher INNA 40 BLANC")) is False
    # в названии другой цвет → не подтверждён
    assert sel.color_confirmed_by_title(black, _p("Вытяжка белая")) is False
    # цвет не запрошен → подтверждать нечего
    assert sel.color_confirmed_by_title(ProductSpec(appliance=ApplianceType.HOOD), _p("X")) is True


def test_title_width_ignores_model_numbers():
    # Голые числа в названии — это номера моделей, а не ширина (#3).
    assert _title_width_mm("Millen MEO 603 WH") is None
    assert _title_width_mm("Lex EDP 607 BL") is None
    assert _title_width_mm("Духовка 60 см белая") == 600
    assert _title_width_mm("Панель 600 мм") == 600


def test_economy_not_fooled_by_model_number_width():
    # «эконом» должен взять дешёвый, а не дорогой с числом «603» в названии.
    sel = ProductSelector(segment="эконом")
    spec = ProductSpec(appliance=ApplianceType.OVEN, color="белый",
                       fuel_type="электрическая", width_mm=600)
    pricey = _p("Электрический духовой шкаф Millen MEO 603 WH", price=50000)
    cheap = _p("Электрический духовой шкаф Krona ESSENZA 60 WH", price=26000)
    ranked = sel.ranked_candidates(spec, [pricey, cheap])
    assert ranked[0] is cheap


def test_beige_and_white_are_distinct():
    # «бежевая» не должна подходить под «белый» и наоборот (#3).
    assert _color_conflict("бежевый", {"белый"}) is True
    assert _color_conflict("белый", {"бежевый"}) is True
    assert _color_conflict("белый", {"белый"}) is False


def test_silver_maps_to_steel():
    assert _canon_color("серебристая") == "нержавейка"
    assert _canon_color("серебристый") == "нержавейка"


def test_install_family_detection():
    assert _install_family("полновстраиваемая") == "встраиваемый"
    assert _install_family("встраиваемый") == "встраиваемый"
    assert _install_family("Отдельностоящий холодильник Liebherr") == "отдельностоящий"
    assert _install_family("чёрный") is None


def test_freestanding_dropped_for_builtin_request():
    # Под «встраиваемый» отдельностоящий отсекается (#2).
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.FRIDGE, install_type="встраиваемый")
    builtin = _p("Встраиваемый холодильник Liebherr ICN", price=100)
    free = _p("Отдельностоящий холодильник Liebherr CNB", price=120)
    ranked = sel.ranked_candidates(spec, [free, builtin])
    assert [c.title for c in ranked] == ["Встраиваемый холодильник Liebherr ICN"]


def test_width_tolerance_595_matches_600():
    # Корпусная ширина 595 мм должна засчитываться под запрос «600».
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.OVEN, width_mm=600)
    a = _p("Духовка A", {"Ширина, мм": "595"}, price=200)
    b = _p("Духовка B", {}, price=200)
    ranked = sel.ranked_candidates(spec, [b, a])  # b раньше по входу
    assert ranked[0].title == "Духовка A"  # совпадение ширины поднимает A выше


# ── Обновление фото в ночном крауле ──────────────────────────────────────────

_PAGE = '''
<html><head><meta property="og:image" content="/ogimage.jpg"></head><body>
<a class="catalog-popup__menu-link" href="/catalog/x/">
  <img src="/upload/iblock/1fc/menu_icon.png"></a>
<div class="slide skeleton">
  <img class="lazyload" src="/upload/iblock/d8a/glavnoe_foto.jpg"></div>
<div class="slide skeleton">
  <img class="lazyload" src="/upload/iblock/827/vtoroe_foto.jpg"></div>
<a href="/products/other/" class="similar__img-box">
  <img src="/upload/iblock/7fa/pohozhiy_tovar.jpg"></a>
</body></html>
'''


def test_parse_image_takes_gallery_photo_not_menu_icon():
    from max_ekrom_bot.ekrom.crawler import _parse_image

    # Иконка меню идёт РАНЬШЕ в HTML, но она без class="lazyload" и в .png.
    assert _parse_image(_PAGE) == "/upload/iblock/d8a/glavnoe_foto.jpg"


def test_parse_image_none_without_photos():
    from max_ekrom_bot.ekrom.crawler import _parse_image

    assert _parse_image("<html><body>нет фото</body></html>") is None


def test_update_image_keeps_attributes(tmp_path):
    # update_enrichment перезаписывает характеристики целиком, поэтому для
    # картинки нужен отдельный метод — иначе ночной прогон стёр бы атрибуты.
    from max_ekrom_bot.ekrom.catalog_cache import CatalogCache
    from max_ekrom_bot.models import CatalogProduct

    cache = CatalogCache(tmp_path / "c.db")
    cache.upsert_many([CatalogProduct(url="/products/a/", title="Духовой шкаф Korting",
                                      price=1000)], "/cat/")
    cache.update_enrichment("/products/a/", {"Цвет": "Черный"}, "/old.jpg")
    cache.update_image("/products/a/", "/new.jpg")

    got = cache.query_category("/cat/")[0]
    assert got.image_url == "/new.jpg"
    assert got.attributes.get("Цвет") == "Черный"      # характеристики целы


def test_urls_missing_image_prioritises_target_brands(tmp_path):
    from max_ekrom_bot.ekrom.catalog_cache import CatalogCache
    from max_ekrom_bot.models import CatalogProduct

    cache = CatalogCache(tmp_path / "c.db")
    cache.upsert_many([
        CatalogProduct(url="/p/bosch/", title="Духовой шкаф Bosch", price=100),
        CatalogProduct(url="/p/korting/", title="Духовой шкаф Korting", price=100),
        CatalogProduct(url="/p/noprice/", title="Духовой шкаф Midea", price=None),
    ], "/cat/")
    urls = cache.urls_missing_image(("korting", "maunfeld"))
    assert urls[0] == "/p/korting/"          # наш бренд — первым
    assert "/p/bosch/" in urls
    assert "/p/noprice/" not in urls          # без цены не подбираем — не грузим


def test_image_pairs_only_with_image(tmp_path):
    from max_ekrom_bot.ekrom.catalog_cache import CatalogCache
    from max_ekrom_bot.models import CatalogProduct

    cache = CatalogCache(tmp_path / "c.db")
    cache.upsert_many([
        CatalogProduct(url="/p/a/", title="A", price=1, image_url="/a.jpg"),
        CatalogProduct(url="/p/b/", title="B", price=1),
    ], "/cat/")
    assert cache.image_pairs() == [("/p/a/", "/a.jpg")]
