"""Тесты правилового парсера (без LLM/сети)."""

from __future__ import annotations

from max_ekrom_bot.models import ApplianceType
from max_ekrom_bot.parsing.rule_parser import RuleBasedParser

EXAMPLE = """\
Добрый день! Можно подбор средний цен. Сегмент
1. Духовка 600 нерж, не чёрную
2. варочная, 3 конфорки. Электрическая
3. Вытяжка полновстроенная белая
4. СВЧ встроенная в пенале с духовкой
"""


def test_parses_example_into_four_specs():
    req = RuleBasedParser().parse(EXAMPLE)
    assert req.segment == "средний"
    assert [s.appliance for s in req.specs] == [
        ApplianceType.OVEN,
        ApplianceType.COOKTOP,
        ApplianceType.HOOD,
        ApplianceType.MICROWAVE,
    ]
    assert req.original_message == EXAMPLE


def test_oven_color_and_width_and_exclusion():
    spec = RuleBasedParser().parse(EXAMPLE).specs[0]
    assert spec.appliance is ApplianceType.OVEN
    assert spec.width_mm == 600
    assert spec.color == "нержавейка"
    assert "чёрный" in spec.exclude_colors


def test_cooktop_burners_and_fuel():
    spec = RuleBasedParser().parse(EXAMPLE).specs[1]
    assert spec.appliance is ApplianceType.COOKTOP
    assert spec.burners == 3
    assert spec.fuel_type == "электрическая"


def test_hood_install_and_color():
    spec = RuleBasedParser().parse(EXAMPLE).specs[2]
    assert spec.appliance is ApplianceType.HOOD
    assert spec.install_type == "полновстраиваемая"
    assert spec.color == "белый"


def test_microwave_detected_before_oven_keyword():
    # «СВЧ ... с духовкой» — СВЧ должно победить, а не духовка.
    spec = RuleBasedParser().parse(EXAMPLE).specs[3]
    assert spec.appliance is ApplianceType.MICROWAVE


def test_width_cm_to_mm():
    req = RuleBasedParser().parse("1. Духовка 60 см белая")
    assert req.specs[0].width_mm == 600


def test_not_negation_only_excludes_color():
    # «не белый» — это исключение, цвет не должен стать желаемым.
    req = RuleBasedParser().parse("1. Вытяжка не белую")
    spec = req.specs[0]
    assert spec.color is None
    assert "белый" in spec.exclude_colors


def test_unknown_lines_are_dropped():
    req = RuleBasedParser().parse("Здравствуйте! Хочу что-нибудь для кухни.")
    assert req.specs == []
