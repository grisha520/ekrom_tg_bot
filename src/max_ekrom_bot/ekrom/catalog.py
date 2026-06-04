"""Карта категорий каталога ekrom.ru и построение URL поиска/фильтров.

URL категорий и путь поиска подтверждены разведкой сайта (Этап 0).
Точные ID фильтров (arrFilter_*) ещё нужно уточнить по HTML формы фильтра —
пока поиск идёт по текстовому запросу через /search/?q=, чего достаточно.
"""

from __future__ import annotations

from urllib.parse import quote_plus, urlencode

from ..models import ApplianceType, ProductSpec

# Подтверждено разведкой: все кухонные категории живут под tekhnika_dlya_kukhni/.
CATEGORY_URLS: dict[ApplianceType, str] = {
    ApplianceType.OVEN: "/catalog/tekhnika_dlya_kukhni/dukhovye_shkafy/",
    ApplianceType.COOKTOP: "/catalog/tekhnika_dlya_kukhni/varochnye_paneli/",
    ApplianceType.HOOD: "/catalog/tekhnika_dlya_kukhni/kukhonnye_vytyazhki/",
    ApplianceType.MICROWAVE: "/catalog/tekhnika_dlya_kukhni/mikrovolnovye_pechi/",
    # Эти две категории на сайте есть, точные slug'и не проверялись разведкой:
    ApplianceType.DISHWASHER: "/catalog/tekhnika_dlya_kukhni/posudomoechnye_mashiny/",
    ApplianceType.FRIDGE: "/catalog/kholodilniki/",  # TODO: уточнить slug
}

# ── Карта фильтров умного фильтра Bitrix (ID подтверждены разведкой) ──────────
# Цвет — общая группа 1734 (есть у духовок, варочных, вытяжек).
_COLOR_GROUP = "1734"
_COLOR_VALUES: dict[str, str] = {
    "нержавейка": "4276650524",
    "нержавеющая сталь": "4276650524",
    "белый": "2377008796",
    "чёрный": "472156207",
    "чёрный матовый": "1184985890",
    "графит": "698014733",
    "бежевый": "2482530932",
    "серый": "2909223739",
    "антрацит": "2765224624",
}

# Тип духового шкафа — группа 1755.
_OVEN_TYPE_GROUP = "1755"
_OVEN_TYPE_VALUES: dict[str, str] = {
    "электрическая": "4098435842",
    "газовая": "3528215300",
}

# Размер духового шкафа — группа 7576 (по ширине).
_OVEN_SIZE_GROUP = "7576"
_OVEN_SIZE_BY_WIDTH: dict[int, str] = {
    600: "245695536",   # Стандартный, см (60×60)
    450: "2544653706",  # Компактный, см (45×60)
    900: "3769328924",  # Широкий, см (90)
}

# Количество конфорок — группа 1762.
_BURNERS_GROUP = "1762"
_BURNERS_VALUES: dict[int, str] = {
    1: "2842311762",
    2: "1230531937",
    3: "2688272468",
    4: "1046437367",
    5: "3611097282",
    6: "3494984923",
    8: "970269123",
}

# Вид варочной поверхности — группа 1925.
_COOKTOP_FUEL_GROUP = "1925"
_COOKTOP_FUEL_VALUES: dict[str, str] = {
    "электрическая": "770404574",
    "газовая": "1445903816",
    "индукционная": "3331432537",
    "комбинированная": "1525432392",
}


def category_for(appliance: ApplianceType) -> str | None:
    """Относительный путь раздела каталога под тип техники."""
    return CATEGORY_URLS.get(appliance)


def _color_param(color: str | None) -> dict[str, str]:
    if not color:
        return {}
    key = color.strip().lower()
    val = _COLOR_VALUES.get(key)
    return {f"arrFilter_{_COLOR_GROUP}_{val}": "Y"} if val else {}


def build_filter_params(spec: ProductSpec) -> dict[str, str]:
    """Собрать GET-параметры умного фильтра под характеристики позиции.

    Применяются только ПОЛОЖИТЕЛЬНЫЕ критерии (нужный цвет, тип, размер,
    конфорки). Исключения (exclude_colors) фильтр не умеет — они отсекаются
    пост-фильтрацией в селекторе. Неизвестные значения пропускаются.
    """
    params: dict[str, str] = {}

    # Цвет — общий для духовок/варочных/вытяжек
    if spec.appliance in (ApplianceType.OVEN, ApplianceType.COOKTOP, ApplianceType.HOOD):
        params.update(_color_param(spec.color))

    if spec.appliance is ApplianceType.OVEN:
        if spec.fuel_type and spec.fuel_type.lower() in _OVEN_TYPE_VALUES:
            params[f"arrFilter_{_OVEN_TYPE_GROUP}_{_OVEN_TYPE_VALUES[spec.fuel_type.lower()]}"] = "Y"
        if spec.width_mm in _OVEN_SIZE_BY_WIDTH:
            params[f"arrFilter_{_OVEN_SIZE_GROUP}_{_OVEN_SIZE_BY_WIDTH[spec.width_mm]}"] = "Y"

    if spec.appliance is ApplianceType.COOKTOP:
        if spec.burners in _BURNERS_VALUES:
            params[f"arrFilter_{_BURNERS_GROUP}_{_BURNERS_VALUES[spec.burners]}"] = "Y"
        if spec.fuel_type and spec.fuel_type.lower() in _COOKTOP_FUEL_VALUES:
            params[f"arrFilter_{_COOKTOP_FUEL_GROUP}_{_COOKTOP_FUEL_VALUES[spec.fuel_type.lower()]}"] = "Y"

    return params


def build_category_url(base_url: str, spec: ProductSpec) -> str | None:
    """URL категории с применёнными фильтрами под позицию (или None, если нет категории)."""
    path = category_for(spec.appliance)
    if not path:
        return None
    url = f"{base_url.rstrip('/')}{path}"
    params = build_filter_params(spec)
    if params:
        params["set_filter"] = "Y"
        url = f"{url}?{urlencode(params)}"
    return url


def build_search_url(base_url: str, query: str) -> str:
    """URL результатов поиска по сайту (fallback)."""
    return f"{base_url.rstrip('/')}/search/?q={quote_plus(query)}"
