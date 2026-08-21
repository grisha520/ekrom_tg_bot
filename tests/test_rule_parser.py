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


# ── Страховка от придуманной ширины (drop_phantom_width) ─────────────────────

def _spec_w(raw: str, width: int | None, **kw):
    from max_ekrom_bot.models import ApplianceType, ProductSpec

    return ProductSpec(appliance=kw.pop("appliance", ApplianceType.COOKTOP),
                       raw_text=raw, width_mm=width, **kw)


def test_burner_count_is_not_width():
    # Живой провал: «варка 4х электрика» → ширина 450, и позиция выпала совсем
    # (45-см электрических варочных на 4 конфорки у бренда нет).
    from max_ekrom_bot.parsing.parser import drop_phantom_width

    spec = _spec_w("варка 4х электрика с зоной расширения", 450, burners=4)
    drop_phantom_width(spec)
    assert spec.width_mm is None
    assert spec.burners == 4          # конфорки остаются


def test_explicit_width_is_kept():
    from max_ekrom_bot.parsing.parser import drop_phantom_width

    for raw, w in (("вытяжка полновстр. 450", 450), ("посудомоечная машина 45 см", 450),
                   ("духовка 450, белая", 450), ("варочная на 60 см", 600)):
        spec = _spec_w(raw, w)
        drop_phantom_width(spec)
        assert spec.width_mm == w, raw


def test_depth_is_not_width():
    # «глубина 31 см» у встраиваемой СВЧ — это глубина, а не ширина.
    from max_ekrom_bot.models import ApplianceType
    from max_ekrom_bot.parsing.parser import drop_phantom_width

    spec = _spec_w("микроволновка встроенная, глубина 31 см, цвет черный", 310,
                   appliance=ApplianceType.MICROWAVE)
    drop_phantom_width(spec)
    assert spec.width_mm is None
    assert spec.extra.get("глубина") == "310"


def test_item_count_is_not_width():
    from max_ekrom_bot.parsing.parser import drop_phantom_width

    spec = _spec_w("комплект из 3 приборов", 300)
    drop_phantom_width(spec)
    assert spec.width_mm is None


def test_no_width_no_change():
    from max_ekrom_bot.parsing.parser import drop_phantom_width

    spec = _spec_w("духовой шкаф", None)
    drop_phantom_width(spec)
    assert spec.width_mm is None


def test_width_recognised_without_space_before_unit():
    # «(500мм)» без пробела: \b между цифрой и кириллицей не срабатывает, и
    # законная ширина терялась — вытяжка на 500 подбиралась как попало.
    from max_ekrom_bot.parsing.parser import drop_phantom_width

    for raw in ("вытяжку (500мм)", "вытяжка 500мм", "вытяжка 50см", "вытяжка 50 см"):
        spec = _spec_w(raw, 500)
        drop_phantom_width(spec)
        assert spec.width_mm == 500, raw


# ── Утечка характеристик между позициями ─────────────────────────────────────

def _spec(appliance, raw, **kw):
    from max_ekrom_bot.models import ProductSpec
    return ProductSpec(appliance=appliance, raw_text=raw, **kw)


def test_leaked_color_and_fuel_dropped_from_neighbour():
    # Живой запрос: цвет и «электро» из строк про духовку/варочную модель
    # приписала заодно вытяжке — та поехала бы искаться в графите.
    from max_ekrom_bot.models import ApplianceType as A
    from max_ekrom_bot.parsing.parser import drop_leaked_attributes

    specs = [
        _spec(A.HOOD, "вытяжка полновстраиваемая 600",
              color="графит", fuel_type="электрическая", install_type="полновстраиваемая"),
        _spec(A.COOKTOP, "варочная панель 3 конфорки, электрическая",
              burners=3, fuel_type="электрическая"),
        _spec(A.OVEN, "духовой шкаф 450 (графит или нержавеющая сталь)", color="графит"),
    ]
    drop_leaked_attributes(specs)

    hood, cooktop, oven = specs
    assert hood.color is None                  # чужой цвет убран
    assert hood.fuel_type is None              # чужое топливо убрано
    assert hood.install_type == "полновстраиваемая"   # своё требование осталось
    assert cooktop.fuel_type == "электрическая" and cooktop.burners == 3
    assert oven.color == "графит"              # цвет назван в своей строке


def test_common_requirement_from_header_is_kept():
    # «всё не чёрное» задано в шапке, а не в строке позиции: характеристики нет
    # ни у кого конкретно — это общее требование, его сбрасывать нельзя.
    from max_ekrom_bot.models import ApplianceType as A
    from max_ekrom_bot.parsing.parser import drop_leaked_attributes

    specs = [
        _spec(A.OVEN, "духовой шкаф 600", color="белый"),
        _spec(A.HOOD, "вытяжка 600", color="белый"),
    ]
    drop_leaked_attributes(specs)
    assert [s.color for s in specs] == ["белый", "белый"]


def test_single_position_untouched():
    from max_ekrom_bot.models import ApplianceType as A
    from max_ekrom_bot.parsing.parser import drop_leaked_attributes

    specs = [_spec(A.OVEN, "духовой шкаф", color="чёрный")]
    drop_leaked_attributes(specs)
    assert specs[0].color == "чёрный"


def test_leaked_burners_dropped():
    from max_ekrom_bot.models import ApplianceType as A
    from max_ekrom_bot.parsing.parser import drop_leaked_attributes

    specs = [
        _spec(A.OVEN, "духовой шкаф 600", burners=4),          # у духовки конфорок нет
        _spec(A.COOKTOP, "варочная панель 4 конфорки", burners=4),
    ]
    drop_leaked_attributes(specs)
    assert specs[0].burners is None and specs[1].burners == 4


def test_leak_detected_even_when_llm_trims_raw_text():
    # Модель обрезает raw_text («духовой шкаф 450» вместо «духовка 450 (графит…)»),
    # и утечку не отличить от общего требования — сверяемся с исходным сообщением.
    from max_ekrom_bot.models import ApplianceType as A
    from max_ekrom_bot.parsing.abbreviations import expand_abbreviations
    from max_ekrom_bot.parsing.parser import drop_leaked_attributes

    message = expand_abbreviations(
        "нужен комплект техники:\n"
        "1.вытяжка полновстр 600\n"
        "2. варка 3-х комф. , электро\n"
        "3. духовка 450  ( графит или с нерж.элементами)\n"
        "4. п/м 450"
    )
    specs = [
        _spec(A.HOOD, "вытяжка полновстраиваемая 600",
              color="графит", install_type="полновстраиваемая"),
        _spec(A.COOKTOP, "варочная панель 3 конфорки, электрическая",
              burners=3, fuel_type="электрическая"),
        _spec(A.OVEN, "духовой шкаф 450", color="графит"),      # raw_text обрезан
        _spec(A.DISHWASHER, "посудомоечная машина 450"),
    ]
    drop_leaked_attributes(specs, message)

    assert specs[0].color is None                       # вытяжке цвет не приписан
    assert specs[0].install_type == "полновстраиваемая"
    assert specs[2].color == "графит"                   # у духовки цвет сохранён
