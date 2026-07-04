"""Схема характеристик категории — что реально можно фильтровать и какие значения.

Считается из обогащённого кэша: по товарам категории собираем атрибуты, их тип
(число / класс энергопотребления / категориальный) и реальные значения. Эта
«легенда» отдаётся LLM-мапперу, чтобы он привязывал запрос клиента к ТОЧНЫМ
именам атрибутов сайта, а не угадывал.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

_MIN_COUNT = 5        # атрибут должен встречаться хотя бы у стольких товаров
_TOP_ATTRS = 30       # сколько атрибутов держим в схеме (по частоте)
_TOP_VALUES = 15      # сколько значений категориального атрибута показываем


def _num(s: object) -> float | None:
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(s))
    return float(m.group().replace(",", ".")) if m else None


def _unit_from_key(key: str) -> str:
    # «Max загрузка белья, кг» → «кг»; «Уровень шума, дБ» → «дБ».
    return key.rsplit(",", 1)[1].strip() if "," in key else ""


def build_category_schema(products) -> dict[str, dict]:
    """{имя_атрибута: {type, ...}} по товарам категории."""
    key_count: Counter = Counter()
    values: dict[str, Counter] = defaultdict(Counter)
    numeric: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [числовых, всего]

    for p in products:
        for k, v in (p.attributes or {}).items():
            if not v:
                continue
            key_count[k] += 1
            values[k][v] += 1
            numeric[k][1] += 1
            if _num(v) is not None:
                numeric[k][0] += 1

    schema: dict[str, dict] = {}
    for key, cnt in key_count.most_common():
        if cnt < _MIN_COUNT or len(schema) >= _TOP_ATTRS:
            continue
        kl = key.lower()
        if "класс энерг" in kl:
            info: dict = {"type": "energy"}
        elif numeric[key][1] and numeric[key][0] >= 0.7 * numeric[key][1]:
            nums = [n for v in values[key] if (n := _num(v)) is not None]
            info = {
                "type": "number",
                "unit": _unit_from_key(key),
                "min": min(nums), "max": max(nums),
            }
        else:
            info = {"type": "categorical",
                    "values": [v for v, _ in values[key].most_common(_TOP_VALUES)]}
        info["count"] = cnt
        schema[key] = info
    return schema


# Мемоизация схем по категории на время жизни процесса (словарь атрибутов меняется
# редко; ночной краул данные освежает, но набор ключей стабилен).
_SCHEMA_MEMO: dict[str, dict] = {}


def get_category_schema(cache, category: str) -> dict[str, dict]:
    """Схема категории из кэша (с мемоизацией). Пусто, если товаров нет."""
    if not category:
        return {}
    if category not in _SCHEMA_MEMO:
        try:
            _SCHEMA_MEMO[category] = build_category_schema(cache.query_category(category))
        except Exception:  # noqa: BLE001
            _SCHEMA_MEMO[category] = {}
    return _SCHEMA_MEMO[category]
