"""Тесты доменных моделей: валидаторы и вычисляемые свойства."""

from __future__ import annotations

from max_ekrom_bot.models import (
    ApplianceType,
    CatalogProduct,
    CustomerRequest,
    MatchedItem,
    ProductSpec,
    SelectionResult,
)


def test_spec_none_coerced_to_empty_collections():
    # LLM нередко присылает null вместо [] / {}.
    spec = ProductSpec.model_validate({"exclude_colors": None, "extra": None})
    assert spec.exclude_colors == []
    assert spec.extra == {}


def test_spec_blank_strings_coerced_to_none():
    # YandexGPT иногда присылает строку "null" вместо JSON null.
    spec = ProductSpec.model_validate({
        "appliance": "oven",
        "color": "null",
        "fuel_type": "NULL",
        "brand": "",
        "model": "-",
        "install_type": "нет",
    })
    assert spec.color is None
    assert spec.fuel_type is None
    assert spec.brand is None
    assert spec.model is None
    assert spec.install_type is None


def test_extra_coerces_nonstring_values():
    # LLM кладёт в extra числа/булевы — не должно падать (как было «Тостер slots:2»).
    spec = ProductSpec.model_validate({"raw_text": "Тостер", "extra": {"slots": 2, "f": True}})
    assert spec.extra == {"slots": "2", "f": "True"}


def test_unknown_appliance_string_coerced_to_unknown():
    assert ProductSpec.model_validate({"appliance": "blender"}).appliance is ApplianceType.UNKNOWN
    assert ProductSpec.model_validate({"appliance": "oven"}).appliance is ApplianceType.OVEN
    assert ProductSpec.model_validate({"appliance": None}).appliance is ApplianceType.UNKNOWN


def test_request_none_specs_coerced_to_empty_list():
    req = CustomerRequest.model_validate({"specs": None})
    assert req.specs == []


def test_matched_item_is_matched_flag():
    p = CatalogProduct(title="A", url="u", price=10)
    assert MatchedItem(spec=ProductSpec(), product=p).is_matched
    assert not MatchedItem(spec=ProductSpec(), product=None).is_matched


def test_selection_result_partitions_and_total():
    p1 = CatalogProduct(title="A", url="u1", price=100)
    p2 = CatalogProduct(title="B", url="u2", price=None)  # без цены → 0 в сумме
    result = SelectionResult(
        items=[
            MatchedItem(spec=ProductSpec(), product=p1),
            MatchedItem(spec=ProductSpec(), product=p2),
            MatchedItem(spec=ProductSpec(), product=None),
        ]
    )
    assert len(result.matched) == 2
    assert len(result.unmatched) == 1
    assert result.total_price == 100


def test_segment_null_string_coerced_to_none():
    req = CustomerRequest.model_validate({"specs": [], "segment": "null", "notes": "none"})
    assert req.segment is None
    assert req.notes is None
    assert CustomerRequest.model_validate({"specs": [], "segment": "премиум"}).segment == "премиум"


def test_attr_constraints_accept_objects_not_only_dicts():
    # Раньше валидатор пропускал ТОЛЬКО словари, и готовые AttrConstraint молча
    # исчезали — код, строящий спеки программно, терял ограничения без ошибки.
    from max_ekrom_bot.models import AttrConstraint, ApplianceType, ProductSpec

    spec = ProductSpec(
        appliance=ApplianceType.HOOD, raw_text="вытяжка",
        attr_constraints=[AttrConstraint(attribute="Тип кухонной вытяжки", op="eq",
                                         value="Полновстраиваемая")],
    )
    assert len(spec.attr_constraints) == 1
    assert spec.attr_constraints[0].attribute == "Тип кухонной вытяжки"

    # Мусор без имени/значения по-прежнему отсеивается.
    spec2 = ProductSpec(appliance=ApplianceType.HOOD, raw_text="в",
                        attr_constraints=[{"attribute": "", "value": "x"},
                                          {"attribute": "Ширина, мм", "value": ""}])
    assert spec2.attr_constraints == []
