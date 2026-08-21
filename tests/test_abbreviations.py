"""Разворот сокращений в запросе клиента (телеграфные формулировки мебельщиков)."""

from __future__ import annotations

from max_ekrom_bot.models import ApplianceType
from max_ekrom_bot.parsing.abbreviations import expand_abbreviations
from max_ekrom_bot.parsing.rule_parser import _detect_appliance

# Реальное сообщение клиента, на котором бот отвечал «не понял».
LIVE_MESSAGE = """нужен комплект техники:
1.вытяжка полновстр 600
2. варка 3-х комф. , электро
3. духовка 450  ( графит или с нерж.элементами)
4. п/м 450"""


def test_dishwasher_shorthand_not_microwave():
    # «п/м 450» уезжало в микроволновку — теперь однозначно посудомойка.
    text = expand_abbreviations("п/м 450")
    assert "посудомоечная машина" in text
    assert _detect_appliance(text) is ApplianceType.DISHWASHER


def test_appliance_shorthands():
    cases = {
        "п/м": ApplianceType.DISHWASHER,
        "пмм": ApplianceType.DISHWASHER,
        "варка": ApplianceType.COOKTOP,
        "в/п": ApplianceType.COOKTOP,
        "духовка": ApplianceType.OVEN,
        "д/ш": ApplianceType.OVEN,
        "х/к": ApplianceType.FRIDGE,
        "свч": ApplianceType.MICROWAVE,
    }
    for short, expected in cases.items():
        assert _detect_appliance(expand_abbreviations(short)) is expected, short


def test_install_and_attribute_shorthands():
    assert "полновстраиваемая" in expand_abbreviations("вытяжка полновстр 600")
    assert "нержавеющая сталь" in expand_abbreviations("духовка с нерж.элементами")
    assert "отдельностоящая" in expand_abbreviations("холодильник отд/ст")
    assert "электрическая" in expand_abbreviations("варка электро")


def test_burners_shorthand():
    # «3-х комф.» → «3 конфорки» (число сохраняется).
    for src in ("3-х комф.", "3х комф", "3 комф.", "4-х комф."):
        out = expand_abbreviations(src)
        assert "конфорки" in out
        assert out[0] in "34"


def test_live_message_all_positions_recognised():
    text = expand_abbreviations(LIVE_MESSAGE)
    lines = [ln for ln in text.splitlines() if ln.strip()][1:]   # без «нужен комплект»
    got = [_detect_appliance(ln) for ln in lines]
    assert got == [
        ApplianceType.HOOD,
        ApplianceType.COOKTOP,
        ApplianceType.OVEN,
        ApplianceType.DISHWASHER,          # было MICROWAVE
    ]


def test_expand_is_idempotent_and_safe():
    # Повторный разворот ничего не ломает, обычный текст не искажается.
    once = expand_abbreviations(LIVE_MESSAGE)
    assert expand_abbreviations(once) == once
    plain = "духовой шкаф чёрный встраиваемый с пиролизом"
    assert expand_abbreviations(plain) == plain
    assert expand_abbreviations("") == ""


def test_does_not_break_words_containing_shorthands():
    # Короткие формы не должны срабатывать внутри слов.
    assert "полновстраиваемая" in expand_abbreviations("полновстраиваемая вытяжка")
    text = expand_abbreviations("смеситель и пмм")
    assert text.startswith("смеситель")           # «см» внутри слова не тронуто
    assert "посудомоечная машина" in text
