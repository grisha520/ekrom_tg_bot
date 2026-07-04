"""Тесты схемо-ориентированного подбора: схема, ограничения, маппер."""

from __future__ import annotations

from max_ekrom_bot.matching.attr_schema import build_category_schema
from max_ekrom_bot.matching.selector import ProductSelector
from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec
from max_ekrom_bot.parsing.attr_mapper import AttrMapper


def _p(title, price, attrs):
    return CatalogProduct(title=title, url=f"u/{title}", price=price, attributes=attrs)


def _washers():
    return [
        _p("Стир A", 100, {"Max загрузка белья, кг": "6", "Тип загрузки": "Фронтальный",
                            "Класс энергопотребления": "A", "Скорость отжима, оборотов/мин": "1000"}),
        _p("Стир B", 100, {"Max загрузка белья, кг": "8", "Тип загрузки": "Фронтальный",
                            "Класс энергопотребления": "A+++", "Скорость отжима, оборотов/мин": "1200"}),
        _p("Стир C", 100, {"Max загрузка белья, кг": "10", "Тип загрузки": "Вертикальный",
                            "Класс энергопотребления": "B", "Скорость отжима, оборотов/мин": "1400"}),
        _p("Стир D", 100, {"Max загрузка белья, кг": "7", "Тип загрузки": "Фронтальный",
                            "Класс энергопотребления": "A++", "Скорость отжима, оборотов/мин": "800"}),
        _p("Стир E", 100, {"Max загрузка белья, кг": "9", "Тип загрузки": "Фронтальный",
                            "Класс энергопотребления": "A+", "Скорость отжима, оборотов/мин": "1600"}),
    ]


def test_schema_infers_types():
    sch = build_category_schema(_washers())
    assert sch["Max загрузка белья, кг"]["type"] == "number"
    assert sch["Max загрузка белья, кг"]["unit"] == "кг"
    assert sch["Класс энергопотребления"]["type"] == "energy"
    assert sch["Тип загрузки"]["type"] == "categorical"
    assert set(sch["Тип загрузки"]["values"]) == {"Фронтальный", "Вертикальный"}


def test_constraint_eq_categorical_hard_filter():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.UNKNOWN,
                       attr_constraints=[{"attribute": "Тип загрузки", "op": "eq", "value": "Вертикальный"}])
    ranked = sel.ranked_candidates(spec, _washers())
    assert [c.title for c in ranked] == ["Стир C"]


def test_constraint_lte_numeric_filter():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.UNKNOWN,
                       attr_constraints=[{"attribute": "Max загрузка белья, кг", "op": "lte", "value": "7"}])
    ranked = sel.ranked_candidates(spec, _washers())
    assert {c.title for c in ranked} == {"Стир A", "Стир D"}  # 6 и 7 кг


def test_constraint_gte_and_energy_equal_or_better():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.UNKNOWN, attr_constraints=[
        {"attribute": "Скорость отжима, оборотов/мин", "op": "gte", "value": "1200"},
        {"attribute": "Класс энергопотребления", "op": "eq", "value": "A+"},
    ])
    ranked = sel.ranked_candidates(spec, _washers())
    # отжим ≥1200 И класс не хуже A+: B(1400) — класс хуже, отсекается; остаются B?,E
    titles = {c.title for c in ranked}
    assert "Стир E" in titles       # 1600, A+
    assert "Стир A" not in titles   # 1000 < 1200
    assert "Стир C" not in titles   # класс B хуже A+


def test_constraint_relaxation_when_empty():
    sel = ProductSelector(segment="средний")
    spec = ProductSpec(appliance=ApplianceType.UNKNOWN,
                       attr_constraints=[{"attribute": "Max загрузка белья, кг", "op": "gte", "value": "50"}])
    # никто не проходит (50 кг нет) → смягчение возвращает всех
    ranked = sel.ranked_candidates(spec, _washers())
    assert len(ranked) == 5


class _FakeLLM:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def complete_json(self, system, user):
        self.calls.append(user)
        return self.resp


def test_mapper_fills_constraints_and_clears_extra():
    schema = build_category_schema(_washers())
    spec = ProductSpec(appliance=ApplianceType.UNKNOWN, raw_text="стиралка на 8 кг фронтальная",
                       extra={"старое": "значение"})
    llm = _FakeLLM('{"map": {"1": [{"attribute": "Max загрузка белья, кг", "op": "approx", "value": "8"}, '
                   '{"attribute": "Тип загрузки", "op": "eq", "value": "Фронтальный"}]}}')
    AttrMapper(llm=llm).map([(spec, schema)])
    assert len(spec.attr_constraints) == 2
    assert spec.attr_constraints[0].attribute == "Max загрузка белья, кг"
    assert spec.extra == {}


def test_mapper_drops_hallucinated_keys():
    schema = build_category_schema(_washers())
    spec = ProductSpec(appliance=ApplianceType.UNKNOWN, raw_text="стиралка")
    llm = _FakeLLM('{"map": {"1": [{"attribute": "Придуманный атрибут", "op": "eq", "value": "X"}]}}')
    AttrMapper(llm=llm).map([(spec, schema)])
    assert spec.attr_constraints == []  # несуществующий ключ отброшен
