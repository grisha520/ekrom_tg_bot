"""Кэш привязки характеристик: экономит Pro-вызовы, не меняя результата."""

from __future__ import annotations

import json

from max_ekrom_bot.config import Settings
from max_ekrom_bot.models import ApplianceType, ProductSpec
from max_ekrom_bot.parsing.attr_map_cache import (
    AttrMapCache, make_key, normalize_text, schema_fingerprint,
)
from max_ekrom_bot.parsing.attr_mapper import AttrMapper

SCHEMA = {
    "Система очистки": {"type": "categorical", "values": ["Пиролиз", "Гидролиз", "Каталитическая"]},
    "Объем, л": {"type": "number", "min": 40, "max": 90},
}
_S = Settings(telegram_bot_token="x", LLM_PROVIDER="rule")


class _CountingLLM:
    """Отвечает фиксированной привязкой и считает вызовы."""

    def __init__(self, answer: dict):
        self.calls = 0
        self.answer = answer

    def complete_json(self, system: str, user: str) -> str:
        self.calls += 1
        return json.dumps(self.answer, ensure_ascii=False)


def _spec(text: str) -> ProductSpec:
    return ProductSpec(appliance=ApplianceType.OVEN, raw_text=text, extra={"очистка": "пиролиз"})


# ── ключ ──────────────────────────────────────────────────────────────────────

def test_normalize_collapses_equivalent_phrasings():
    a = normalize_text("Духовой шкаф, чёрный 60 см")
    b = normalize_text("духовой  шкаф черный 60см.")
    assert a == b


def test_key_changes_with_schema_fingerprint():
    # После краула у категории появился атрибут — старые ответы не должны подойти.
    k1 = make_key("духовой шкаф с пиролизом", SCHEMA)
    changed = dict(SCHEMA, **{"Гриль": {"type": "categorical", "values": ["Да", "Нет"]}})
    k2 = make_key("духовой шкаф с пиролизом", changed)
    assert k1 != k2
    assert schema_fingerprint(SCHEMA) != schema_fingerprint(changed)


def test_fingerprint_stable_to_value_order():
    s2 = {"Система очистки": {"type": "categorical", "values": ["Гидролиз", "Пиролиз", "Каталитическая"]},
          "Объем, л": {"type": "number", "min": 40, "max": 90}}
    assert schema_fingerprint(SCHEMA) == schema_fingerprint(s2)


# ── хранилище ─────────────────────────────────────────────────────────────────

def test_cache_roundtrip_and_hits(tmp_path):
    cache = AttrMapCache(tmp_path / "c.db")
    assert cache.get("духовой шкаф с пиролизом", SCHEMA) is None
    cache.put("духовой шкаф с пиролизом", SCHEMA,
              [{"attribute": "Система очистки", "op": "eq", "value": "Пиролиз"}])
    got = cache.get("Духовой шкаф, с пиролизом", SCHEMA)      # другая форма — тот же ключ
    assert got == [{"attribute": "Система очистки", "op": "eq", "value": "Пиролиз"}]
    assert cache.stats() == {"entries": 1, "hits": 1}


def test_empty_answer_is_cached_too(tmp_path):
    # Модель сказала «ограничений нет» — это тоже оплаченный ответ.
    cache = AttrMapCache(tmp_path / "c.db")
    cache.put("духовой шкаф", SCHEMA, [])
    assert cache.get("духовой шкаф", SCHEMA) == []


def test_purge_stale_keeps_live_schemas(tmp_path):
    cache = AttrMapCache(tmp_path / "c.db")
    other = {"Тип": {"type": "categorical", "values": ["А"]}}
    cache.put("x", SCHEMA, [])
    cache.put("y", other, [])
    removed = cache.purge_stale({schema_fingerprint(SCHEMA)})
    assert removed == 1
    assert cache.get("x", SCHEMA) == [] and cache.get("y", other) is None


# ── маппер с кэшем ────────────────────────────────────────────────────────────

def test_second_identical_request_skips_llm(tmp_path):
    llm = _CountingLLM({"map": {"1": [{"attribute": "Система очистки", "op": "eq", "value": "Пиролиз"}]}})
    mapper = AttrMapper(llm=llm, settings=_S, cache=AttrMapCache(tmp_path / "c.db"))

    s1 = _spec("духовой шкаф с пиролизом")
    mapper.map([(s1, SCHEMA)], original_message="духовой шкаф с пиролизом")
    assert llm.calls == 1
    assert [c.attribute for c in s1.attr_constraints] == ["Система очистки"]

    s2 = _spec("духовой шкаф с пиролизом")
    mapper.map([(s2, SCHEMA)], original_message="Духовой шкаф с пиролизом.")
    assert llm.calls == 1                                    # из кэша, без LLM
    assert [c.attribute for c in s2.attr_constraints] == ["Система очистки"]
    assert s2.extra == {}                                    # результат тот же, что у LLM-пути


def test_only_misses_go_to_llm(tmp_path):
    # Из двух позиций одна уже в кэше — в LLM уходит только вторая.
    cache = AttrMapCache(tmp_path / "c.db")
    cache.put("духовой шкаф с пиролизом", SCHEMA,
              [{"attribute": "Система очистки", "op": "eq", "value": "Пиролиз"}])

    class _SpyLLM(_CountingLLM):
        def complete_json(self, system, user):
            self.last_user = user
            return super().complete_json(system, user)

    llm = _SpyLLM({"map": {"2": [{"attribute": "Объем, л", "op": "gte", "value": "70"}]}})
    mapper = AttrMapper(llm=llm, settings=_S, cache=cache)
    a, b = _spec("духовой шкаф с пиролизом"), _spec("духовой шкаф от 70 литров")
    mapper.map([(a, SCHEMA), (b, SCHEMA)], original_message="духовка с пиролизом и духовка от 70 л")

    assert llm.calls == 1
    assert "ПОЗИЦИЯ 1" not in llm.last_user and "ПОЗИЦИЯ 2" in llm.last_user
    assert [c.attribute for c in a.attr_constraints] == ["Система очистки"]
    assert [c.attribute for c in b.attr_constraints] == ["Объем, л"]


def test_sanity_check_applies_to_cached_entries(tmp_path):
    # В кэше лежит сырой ответ; проверка по схеме выполняется при чтении, поэтому
    # ограничение с невозможным значением отбрасывается и из кэша.
    cache = AttrMapCache(tmp_path / "c.db")
    cache.put("духовой шкаф на 1000 литров", SCHEMA,
              [{"attribute": "Объем, л", "op": "approx", "value": "1000"}])
    llm = _CountingLLM({"map": {}})
    mapper = AttrMapper(llm=llm, settings=_S, cache=cache)
    s = _spec("духовой шкаф на 1000 литров")
    mapper.map([(s, SCHEMA)], original_message="духовой шкаф на 1000 литров")
    assert llm.calls == 0
    assert s.attr_constraints == []                          # отброшено sanity-check


def test_mapper_without_cache_unchanged(tmp_path):
    llm = _CountingLLM({"map": {"1": []}})
    mapper = AttrMapper(llm=llm, settings=_S, cache=None)
    for _ in range(2):
        mapper.map([(_spec("духовой шкаф"), SCHEMA)], original_message="духовой шкаф")
    assert llm.calls == 2


def test_normalize_keeps_decimal_numbers():
    # «36,6» и «36.6» — одно число; запятая между словами при этом убирается.
    assert normalize_text("гарантия 36,6 мес") == normalize_text("гарантия 36.6 мес")
    assert "36.6" in normalize_text("гарантия 36,6 мес")
