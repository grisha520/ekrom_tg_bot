"""Тесты сборки URL категорий и параметров умного фильтра Bitrix."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from max_ekrom_bot.ekrom.catalog import (
    build_category_url,
    build_filter_params,
    build_search_url,
    category_for,
)
from max_ekrom_bot.models import ApplianceType, ProductSpec

BASE = "https://www.ekrom.ru"


def test_category_for_known_types():
    assert category_for(ApplianceType.OVEN).endswith("/dukhovye_shkafy/")
    assert category_for(ApplianceType.COOKTOP).endswith("/varochnye_paneli/")
    assert category_for(ApplianceType.UNKNOWN) is None


def test_oven_filters_color_fuel_size():
    spec = ProductSpec(
        appliance=ApplianceType.OVEN,
        color="нержавейка",
        fuel_type="электрическая",
        width_mm=600,
    )
    params = build_filter_params(spec)
    # цвет (группа 1734), тип (1755), размер (7576)
    assert "arrFilter_1734_4276650524" in params
    assert "arrFilter_1755_4098435842" in params
    assert "arrFilter_7576_245695536" in params


def test_cooktop_filters_burners_and_fuel():
    spec = ProductSpec(
        appliance=ApplianceType.COOKTOP, burners=3, fuel_type="индукционная"
    )
    params = build_filter_params(spec)
    assert "arrFilter_1762_2688272468" in params  # 3 конфорки
    assert "arrFilter_1925_3331432537" in params  # индукционная


def test_unknown_values_skipped():
    spec = ProductSpec(appliance=ApplianceType.OVEN, color="бирюзовый", width_mm=123)
    assert build_filter_params(spec) == {}


def test_build_category_url_adds_set_filter():
    spec = ProductSpec(appliance=ApplianceType.OVEN, color="белый")
    url = build_category_url(BASE, spec)
    qs = parse_qs(urlsplit(url).query)
    assert qs["set_filter"] == ["Y"]
    assert "arrFilter_1734_2377008796" in qs


def test_build_category_url_none_for_unknown():
    assert build_category_url(BASE, ProductSpec(appliance=ApplianceType.UNKNOWN)) is None


def test_build_category_url_without_filters_has_no_query():
    spec = ProductSpec(appliance=ApplianceType.OVEN)
    assert build_category_url(BASE, spec) == f"{BASE}/catalog/tekhnika_dlya_kukhni/dukhovye_shkafy/"


def test_build_search_url_encodes_query():
    url = build_search_url(BASE, "Korting OGG 771")
    assert url.startswith(f"{BASE}/search/?q=")
    assert "Korting" in url and "%20" not in url  # quote_plus → '+'
