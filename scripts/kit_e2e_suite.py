"""Комплектный end-to-end: parse → select_kit → format_kit → PDF (боевой поток).

Повторяет путь клиента в Telegram-боте (KIT_MODE) на реальных формулировках:
LLM-разбор запроса, сборка комплекта одного бренда по акциям, клиентское
сообщение и PDF. Для каждого кейса — автопроверки покрытия, бренда, честности
расчёта (итог сходится с выгодой, у применённой акции есть срок) и размера PDF.

Запуск (на сервере с .env и catalog.db):
  ./.venv/bin/python scripts/kit_e2e_suite.py
  ./.venv/bin/python scripts/kit_e2e_suite.py --html data/out/kit_e2e.html
"""

from __future__ import annotations

import argparse
import asyncio
import html as html_lib
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from max_ekrom_bot.config import get_settings  # noqa: E402
from max_ekrom_bot.messenger.client_ux import format_kit  # noqa: E402
from max_ekrom_bot.pipeline import Pipeline  # noqa: E402

TARGET_BRANDS = ("Korting", "Maunfeld", "Midea", "Lex", "Kuppersberg")


@dataclass
class KitCase:
    """Кейс прогона: формулировка клиента + что обязаны получить на выходе."""

    query: str
    min_cover: int = 1                       # минимум закрытых позиций
    brands: tuple[str, ...] | None = None    # допустимые бренды комплекта (или любые из 5)
    expect_manager_specs: bool = False       # должны быть позиции «менеджеру» (вне охвата)
    allow_empty: bool = False                # расплывчатый запрос: допустимо без комплекта
    # Требования клиента, которые обязаны быть соблюдены В ТОВАРАХ комплекта.
    forbid_colors: tuple[str, ...] = ()      # «не чёрное» — таких цветов быть не должно
    require_attr: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # {атрибут: допустимые значения} — проверяем у позиций, где атрибут заполнен
    max_item_price: int | None = None        # «до 30000» — потолок цены позиции
    expect_gift: bool = False                # ожидаем подарок по акции


# Реальные формулировки клиентов (из примеров онбординга, эталонных подборок
# и типовых обращений). Порядок — от базовых к каверзным.
CASES: list[KitCase] = [
    KitCase("духовой шкаф, варочная панель и вытяжка", min_cover=3),
    KitCase("холодильник, посудомойка, духовка и варочная", min_cover=4),
    KitCase("можно Kuppersberg Maunfeld: духовой шкаф, варочная панель, вытяжка, "
            "посудомоечная машина", min_cover=4, brands=("Kuppersberg", "Maunfeld")),
    KitCase("комплект: духовка белая, варочная панель чёрная и вытяжка", min_cover=3),
    KitCase("духовой шкаф и варочная панель Korting", min_cover=2, brands=("Korting",)),
    KitCase("духовка, варочная, вытяжка, посудомойка 45 см и холодильник", min_cover=5),
    KitCase("варочная панель домино и духовой шкаф", min_cover=2),
    KitCase("духовка, варочная, вытяжка и мойка со смесителем", min_cover=3,
            expect_manager_specs=True),
    KitCase("стиральная машина и сушильная машина Midea", min_cover=1),
    KitCase("духовка до 30000 и варочная до 20000", min_cover=2),
    KitCase("микроволновка, духовка и варочная", min_cover=3),
    KitCase("хочу технику для кухни", allow_empty=True),

    # ── Сокращения и телеграфный стиль (как пишут мебельщики) ────────────────
    KitCase("1.вытяжка полновстр 600\n2. варка 3-х комф. , электро\n"
            "3. духовка 450\n4. п/м 450", min_cover=4),
    KitCase("нужны д/ш 600, в/п газовая 600, п/м 600", min_cover=3),
    KitCase("х/к встраиваемый, духовка, варка индукция", min_cover=3),
    KitCase("свч встройка 600 + духовой шкаф нерж", min_cover=2),

    # ── Цвет и его исключения ───────────────────────────────────────────────
    KitCase("духовка, варочная и вытяжка — всё белое", min_cover=3,
            forbid_colors=("чёрный",)),
    KitCase("комплект техники, только не чёрное: духовка, варочная, вытяжка",
            min_cover=3, forbid_colors=("чёрный",)),
    KitCase("духовой шкаф нержавейка и варочная панель нержавейка", min_cover=2),
    KitCase("вытяжка белая, духовка белая, посудомойка", min_cover=3,
            forbid_colors=("чёрный",)),

    # ── Тип вытяжки (частая ошибка: полновстраиваемая вместо наклонной) ──────
    KitCase("вытяжка наклонная 600 и духовой шкаф", min_cover=2,
            require_attr={"Тип кухонной вытяжки": ("Наклонная",)}),
    KitCase("вытяжка купольная и варочная панель", min_cover=2,
            require_attr={"Тип кухонной вытяжки": ("Купольная",)}),
    KitCase("вытяжка полновстраиваемая 60 см, духовка, варочная", min_cover=3,
            require_attr={"Тип кухонной вытяжки": ("Полновстраиваемая",)}),
    KitCase("вытяжка отдельная или скошенная или труба, духовка, варочная",
            min_cover=3,
            require_attr={"Тип кухонной вытяжки": ("Наклонная", "Купольная",
                                                   "Настенная-тубус", "Островная",
                                                   "Т-образная")}),

    # ── Топливо и конфорки ──────────────────────────────────────────────────
    KitCase("газовая варочная панель 4 конфорки и электрический духовой шкаф",
            min_cover=2, require_attr={"Вид варочной поверхности": ("Газовая",)}),
    KitCase("индукционная варочная панель и духовка с конвекцией", min_cover=2,
            require_attr={"Вид варочной поверхности": ("Индукционная",)}),
    KitCase("варочная на 2 конфорки домино и духовой шкаф 45 см", min_cover=2),

    # ── Установка: встраиваемое против отдельностоящего ─────────────────────
    KitCase("встраиваемый холодильник, духовка и варочная", min_cover=3,
            require_attr={"Тип установки": ("Встраиваемый",)}),
    KitCase("отдельностоящий холодильник side by side и духовой шкаф", min_cover=2),
    KitCase("отдельностоящая стиральная машина с сушкой", min_cover=1),

    # ── Бюджет ──────────────────────────────────────────────────────────────
    KitCase("духовой шкаф до 30000 и вытяжка до 15000", min_cover=2),
    KitCase("недорогой комплект: духовка, варочная, вытяжка до 60000 за всё",
            min_cover=3),

    # ── Габариты ────────────────────────────────────────────────────────────
    KitCase("посудомоечная машина 45 см и духовой шкаф 60 см", min_cover=2),
    KitCase("узкая посудомойка 450, компактная духовка, вытяжка 500", min_cover=2),

    # ── Объём/класс/шум — характеристики за пределами базовых полей ──────────
    KitCase("холодильник от 300 литров No Frost и духовой шкаф", min_cover=2),
    KitCase("посудомойка на 14 комплектов и тихая вытяжка", min_cover=2),
    KitCase("духовой шкаф с пиролизом и варочная с зоной расширения", min_cover=2),

    # ── Акции: подарок и спеццена ───────────────────────────────────────────
    KitCase("Midea: духовка, варочная, вытяжка, свч и посудомойка", min_cover=5,
            brands=("Midea",)),
    KitCase("Kuppersberg комплект: духовка, варочная, вытяжка, посудомойка "
            "и встраиваемый холодильник", min_cover=5, brands=("Kuppersberg",)),

    # ── Вне охвата (должно уходить менеджеру) ───────────────────────────────
    KitCase("мойка из нержавейки, смеситель и измельчитель", allow_empty=True,
            expect_manager_specs=True),
    KitCase("варочная панель Smeg и духовой шкаф Bosch", allow_empty=True,
            expect_manager_specs=True),
    KitCase("духовка, варочная и фильтр для воды", min_cover=2,
            expect_manager_specs=True),

    # ── Каверзные и «грязные» формулировки ──────────────────────────────────
    KitCase("добрый день! подскажите пожалуйста комплект на кухню: духовку, "
            "варочную поверхность и вытяжку, спасибо", min_cover=3),
    KitCase("духовкa, варочнaя, вытяжкa", min_cover=2),          # латиница в словах
    KitCase("ДУХОВОЙ ШКАФ, ВАРОЧНАЯ ПАНЕЛЬ, ВЫТЯЖКА", min_cover=3),
    KitCase("духовой шкаф", min_cover=1),                        # одна позиция
    KitCase("вытяжка, вытяжка, вытяжка", min_cover=1),           # дубли

    # ═══════════════════════════════════════════════════════════════════════
    # Расширение 13.09.2026: покрыть КАЖДУЮ ветку логики подбора (см.
    # docs/ЛОГИКА_ПОДБОРА.md) живыми формулировками — как пишут люди.
    # ═══════════════════════════════════════════════════════════════════════

    # ── Живые сообщения целиком: приветствие + список + хвост ────────────────
    KitCase("Добрый день! Нужен комплект:\n1. Варочная поверхность газовая нержавейка\n"
            "2. Духовой шкаф 600\n3. Встраиваемая СВЧ 600\n4. Посудомойка 450 встраиваемая\n"
            "5. полновстраиваемая вытяжка", min_cover=5,
            require_attr={"Вид варочной поверхности": ("Газовая",),
                          "Тип кухонной вытяжки": ("Полновстраиваемая",)}),
    KitCase("Здравствуйте, подскажите пожалуйста, хочу на кухню духовку, варочную и вытяжку. "
            "Заранее спасибо!", min_cover=3),
    KitCase("нужно: большой холодильник 2камеротдельный\nварочная на 600мм ГАЗОВАЯ встроенная\n"
            "духовка встроеннаяна 600мм электртческая\nсвч встроенная на 600мм\n"
            "вытяжка отдельная или скошеннаая или труба\nвсе это не черное", min_cover=5,
            forbid_colors=("чёрный",),
            require_attr={"Тип кухонной вытяжки": ("Наклонная", "Купольная", "Настенная-тубус",
                                                   "Островная", "Т-образная")}),
    KitCase("Комплект на кухню под заказ: духовой шкаф, индукционная панель, вытяжка, ПММ 60. "
            "Бюджет примерно 150 тысяч на всё.", min_cover=4,
            require_attr={"Вид варочной поверхности": ("Индукционная",)}),

    # ── Сокращения: все формы из словаря ────────────────────────────────────
    KitCase("д/ш, в/п, вытяжка, п/м", min_cover=4),
    KitCase("пмм 450 и духовка 600", min_cover=2),
    KitCase("х/к отд/ст, духовой, варка индукц.", min_cover=3),
    KitCase("свч встройка + духовка встройка + варка эл. 600", min_cover=3),
    KitCase("вытяжка п/в 600, духовка 600, варочная 4-х конф газ.", min_cover=3,
            require_attr={"Тип кухонной вытяжки": ("Полновстраиваемая",),
                          "Вид варочной поверхности": ("Газовая",)}),

    # ── Цвет: каждый из распознаваемых + исключения ─────────────────────────
    KitCase("духовка графит и варочная графит", min_cover=2),
    KitCase("духовой шкаф бежевый, вытяжка бежевая, варочная слоновая кость", min_cover=2),
    KitCase("комплект в цвете нержавеющая сталь: духовка, варочная, вытяжка, свч", min_cover=4),
    KitCase("духовка, варочная, вытяжка — не белые и не бежевые", min_cover=3,
            forbid_colors=("белый", "бежевый")),
    KitCase("хочу серую духовку и серую вытяжку", min_cover=2),
    KitCase("духовой шкаф чёрное стекло, варочная чёрная, вытяжка чёрная", min_cover=3),

    # ── Размеры: все ходовые ширины и формы записи ───────────────────────────
    KitCase("посудомойка 60 см и духовка 60", min_cover=2),
    KitCase("вытяжка 900 и варочная 900 газовая", min_cover=2),
    KitCase("вытяжка 500 мм", min_cover=1),
    KitCase("духовка 45см компактная и свч 60см", min_cover=2),
    KitCase("варочная панель 30 см домино и духовка", min_cover=2),
    KitCase("посудомоечная машина узкая 45 и вытяжка 60", min_cover=2),

    # ── Топливо и конфорки ──────────────────────────────────────────────────
    KitCase("варочная газовая 2 конфорки и духовка", min_cover=2,
            require_attr={"Вид варочной поверхности": ("Газовая",)}),
    KitCase("варочная на 5 конфорок газ и духовой шкаф", min_cover=2),
    KitCase("комбинированная варочная (газ + электро) и духовка", min_cover=2),
    KitCase("варочная любая кроме газовой, духовка электрическая", min_cover=2),
    KitCase("газовый духовой шкаф и газовая варочная", min_cover=1),   # газовых духовок мало

    # ── Установка ───────────────────────────────────────────────────────────
    KitCase("отдельностоящий холодильник двухкамерный ноу фрост и духовка", min_cover=2),
    KitCase("встраиваемый холодильник 178 см, встраиваемая посудомойка, духовка", min_cover=3,
            require_attr={"Тип установки": ("Встраиваемый",)}),
    KitCase("отдельностоящая посудомоечная машина 60 и духовой шкаф", min_cover=2),
    KitCase("свч отдельностоящая соло и духовка", min_cover=2),

    # ── Типы вытяжек: каждый из словаря ─────────────────────────────────────
    KitCase("вытяжка каминная 60 и духовка", min_cover=2,
            require_attr={"Тип кухонной вытяжки": ("Купольная",)}),
    KitCase("вытяжка островная 90 и варочная 90", min_cover=2,
            require_attr={"Тип кухонной вытяжки": ("Островная",)}),
    KitCase("вытяжка т-образная и духовой шкаф", min_cover=2,
            require_attr={"Тип кухонной вытяжки": ("Т-образная",)}),
    KitCase("вытяжка тубус настенная 60 и варочная", min_cover=2,
            require_attr={"Тип кухонной вытяжки": ("Настенная-тубус",)}),
    KitCase("вытяжка встраиваемая в шкаф 60, духовка, варочная", min_cover=3,
            require_attr={"Тип кухонной вытяжки": ("Встраиваемая", "Полновстраиваемая")}),

    # ── Бренды: каждый из пяти, пары, чужие, «кроме» ────────────────────────
    KitCase("Lex: духовка, варочная, вытяжка", min_cover=3, brands=("Lex",)),
    KitCase("хочу Midea — духовой шкаф, варочная панель, вытяжка, свч", min_cover=4,
            brands=("Midea",)),
    KitCase("Korting или Kuppersberg: духовка, варочная, вытяжка, посудомойка", min_cover=4,
            brands=("Korting", "Kuppersberg")),
    KitCase("Maunfeld духовка + Maunfeld варочная + вытяжка Maunfeld", min_cover=3,
            brands=("Maunfeld",)),
    KitCase("духовка Bosch, варочная Siemens, вытяжка Elica", allow_empty=True,
            expect_manager_specs=True),
    KitCase("духовка и варочная, только не Lex", min_cover=2),
    KitCase("Куперсберг духовка и варочная", min_cover=2),           # кириллицей

    # ── Бюджет: на позицию, на всё, «недорого» ───────────────────────────────
    KitCase("духовка до 40 тысяч, варочная до 25 тысяч, вытяжка до 15", min_cover=3),
    KitCase("самый дешёвый комплект: духовка, варочная, вытяжка", min_cover=3),
    KitCase("премиум комплект: духовка, варочная, вытяжка, посудомойка", min_cover=4),
    KitCase("духовой шкаф до 20000 руб", min_cover=1),               # ниже минимума бренда

    # ── Характеристики за пределами базовых полей ───────────────────────────
    KitCase("духовой шкаф с функцией пара и варочная с бустером", min_cover=2),
    KitCase("посудомойка с третьей корзиной и авто-открыванием, духовка", min_cover=2),
    KitCase("холодильник с зоной свежести от 350 л и духовка с телескопическими направляющими",
            min_cover=2),
    KitCase("вытяжка с периметральным всасыванием тихая, духовка с грилем", min_cover=2),
    KitCase("духовой шкаф объёмом от 70 литров, посудомойка на 10 комплектов", min_cover=2),

    # ── Акции: каждая механика должна иметь шанс сработать ──────────────────
    KitCase("Korting: духовка, варочная, вытяжка, посудомойка, холодильник", min_cover=5,
            brands=("Korting",)),                                    # каскад до 100 %
    KitCase("Maunfeld: духовка, варочная, вытяжка, посудомойка, свч, холодильник",
            min_cover=5, brands=("Maunfeld",)),                      # плоская до 15 %
    KitCase("Kuppersberg: духовка, варочная, вытяжка и встраиваемый холодильник",
            min_cover=4, brands=("Kuppersberg",)),                   # спеццена холодильника
    KitCase("Korting духовой шкаф, варочная панель и микроволновка", min_cover=3,
            brands=("Korting",)),                                    # подарок «Формула выгоды»

    # ── Вне охвата и смесь ──────────────────────────────────────────────────
    KitCase("мойка гранитная чёрная 60 см", allow_empty=True, expect_manager_specs=True),
    KitCase("стиральная машина 8 кг с сушкой и духовка", min_cover=1),
    KitCase("винный шкаф на 30 бутылок и духовой шкаф", min_cover=1),
    KitCase("духовка, варочная, вытяжка, кофемашина встраиваемая", min_cover=3),
    KitCase("измельчитель отходов, смеситель, посудомойка 45", min_cover=1,
            expect_manager_specs=True),

    # ── Каверзные ───────────────────────────────────────────────────────────
    KitCase("духовка", min_cover=1),
    KitCase("Духовка!!! Варочная!!! Вытяжка!!!", min_cover=3),
    KitCase("духовка,варочная,вытяжка,посудомойка,холодильник,свч", min_cover=5),   # без пробелов
    KitCase("1) духовка 2) варочная 3) вытяжка", min_cover=3),
    KitCase("• духовка\n• варочная\n• вытяжка", min_cover=3),
    KitCase("хочу такой же комплект как у соседа: духовка и варочная", min_cover=2),
    KitCase("нужна духовка. и варочная. и вытяжка тоже нужна.", min_cover=3),
    KitCase("ДУХОВОЙ шкаф чЁрный + ВАРОЧНАЯ", min_cover=2),
    KitCase("посоветуйте что-нибудь", allow_empty=True),
    KitCase("сколько стоит духовка?", min_cover=1),
    KitCase("2 духовки и варочная", min_cover=2),                    # количество
    KitCase("духовка (не встраиваемая, отдельно стоящая) и варочная", min_cover=1),
]


@dataclass
class CaseResult:
    query: str
    ok: bool
    elapsed_s: float
    specs: int = 0
    coverage: int = 0
    brand: str | None = None
    campaign: str | None = None
    mechanic: str | None = None
    subtotal: float | None = None
    benefit: float | None = None
    final: float | None = None
    gift: str | None = None
    manager_review: bool = False
    manager_specs: int = 0
    products: list[str] = field(default_factory=list)
    message_len: int = 0
    pdf_bytes: int | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)


def _honesty_notes(combo) -> list[str]:
    """Инварианты расчёта: применённая акция обязана сходиться в рублях."""
    notes: list[str] = []
    if combo.applied:
        if combo.discount_total <= 0:
            notes.append("акция применена, но выгода ≤ 0")
        deducted = abs(combo.final_total - (combo.subtotal - combo.discount_total)) < 0.01
        on_top = bool(combo.gift_title) and abs(combo.final_total - combo.subtotal) < 0.01
        if not (deducted or on_top):
            notes.append("итог не сходится: ни вычет, ни подарок-сверху")
        if not combo.campaign_slug:
            notes.append("применённая акция без slug")
    else:
        if abs(combo.final_total - combo.subtotal) > 0.01:
            notes.append("акция не применена, но итог изменён")
    return notes


def _requirement_notes(case: KitCase, kit) -> list[str]:
    """Соблюдены ли требования клиента В САМИХ ТОВАРАХ комплекта.

    Проверяем по фактическим карточкам, а не по разбору: клиент судит по тому,
    что ему прислали. Пустой атрибут не считаем нарушением — у части карточек
    характеристики не заполнены, и придираться к молчанию нельзя.
    """
    from max_ekrom_bot.matching.selector import _canon_color, _product_colors

    notes: list[str] = []
    for item in kit.selection.matched:
        product = item.product
        title = product.title[:40]
        attrs = product.attributes or {}

        for forbidden in case.forbid_colors:
            want = _canon_color(forbidden)
            if want in _product_colors(product):
                notes.append(f"запрещённый цвет «{forbidden}»: {title}")

        for attr, allowed in case.require_attr.items():
            value = attrs.get(attr)
            if value and not any(a.lower() in value.lower() for a in allowed):
                notes.append(f"{attr} = «{value}», ожидали {allowed}: {title}")

        if case.max_item_price and (product.price or 0) > case.max_item_price:
            notes.append(f"дороже потолка {case.max_item_price}: {title}")

    if case.expect_gift and not kit.combo.gift_title:
        notes.append("ожидали подарок по акции — его нет")
    return notes


async def run_case(pipeline: Pipeline, case: KitCase) -> CaseResult:
    t0 = time.perf_counter()
    res = CaseResult(query=case.query, ok=False, elapsed_s=0.0)
    try:
        request = pipeline.parse_message(case.query)
        res.specs = len(request.specs)
        kit = await asyncio.wait_for(pipeline.select_kit(request), timeout=240)

        if kit is None or kit.coverage == 0:
            res.ok = case.allow_empty
            if not res.ok:
                res.notes.append("комплект не собран")
            res.elapsed_s = round(time.perf_counter() - t0, 2)
            return res

        res.coverage = kit.coverage
        res.brand = kit.brand
        res.manager_specs = len(kit.manager_specs or [])
        combo = kit.combo
        res.campaign = combo.campaign_slug
        res.mechanic = combo.mechanic
        res.subtotal, res.benefit, res.final = combo.subtotal, combo.discount_total, combo.final_total
        res.gift = combo.gift_title
        res.manager_review = combo.manager_review
        res.products = [m.product.title[:80] for m in kit.selection.matched if m.product]

        # Клиентское сообщение должно рендериться (это то, что видит человек).
        msg = format_kit(kit, get_settings())
        res.message_len = len(msg)

        # PDF — боевой путь: HTML→Chromium, фолбэк reportlab (как в telegram_app).
        try:
            pdf_path = await pipeline.build_kit_pdf_html(kit, None)
        except Exception:  # noqa: BLE001 — фолбэк как в боте
            pdf_path = await asyncio.to_thread(pipeline.build_kit_pdf, kit, None)
        res.pdf_bytes = pdf_path.stat().st_size

        # ── Автопроверки ──
        if res.coverage < case.min_cover:
            res.notes.append(f"покрытие {res.coverage} < {case.min_cover}")
        if kit.brand not in TARGET_BRANDS:
            res.notes.append(f"бренд вне пятёрки: {kit.brand!r}")
        if case.brands and kit.brand not in case.brands:
            res.notes.append(f"бренд {kit.brand} вне разрешённых {case.brands}")
        if case.expect_manager_specs and not res.manager_specs:
            res.notes.append("ожидали позиции для менеджера — их нет")
        res.notes += _requirement_notes(case, kit)
        if res.message_len < 100:
            res.notes.append("подозрительно короткое сообщение клиенту")
        if (res.pdf_bytes or 0) <= 5000:
            res.notes.append("PDF слишком маленький")
        res.notes += _honesty_notes(combo)
        res.ok = not res.notes
    except Exception as exc:  # noqa: BLE001
        res.error = repr(exc)[:300]
    res.elapsed_s = round(time.perf_counter() - t0, 2)
    return res


def _html_report(results: list[CaseResult], settings) -> str:
    passed = sum(1 for r in results if r.ok)
    rows = []
    for i, r in enumerate(results, 1):
        cls = "ok" if r.ok else "fail"
        prods = "<br>".join(html_lib.escape(p) for p in r.products) or "—"
        money = (f"{r.subtotal:,.0f} − {r.benefit:,.0f} = {r.final:,.0f}".replace(",", " ")
                 if r.subtotal is not None else "—")
        note = html_lib.escape("; ".join(r.notes) or r.error or "")
        rows.append(
            f"<tr class='{cls}'><td>{i}</td><td>{html_lib.escape(r.query)}</td>"
            f"<td>{'✓' if r.ok else '✗'}</td><td>{r.brand or '—'}</td>"
            f"<td>{r.coverage}/{r.specs}</td>"
            f"<td>{html_lib.escape(r.campaign or '—')}<br>{r.mechanic or ''}</td>"
            f"<td>{money}</td><td>{html_lib.escape(r.gift or '—')}</td>"
            f"<td>{r.pdf_bytes or '—'}</td><td>{r.elapsed_s}s</td><td>{note}</td></tr>"
        )
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>E2E комплекты — max-ekrom-bot</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; background: #0f172a; color: #e2e8f0; }}
h1 {{ font-size: 1.4rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #334155; padding: 8px; vertical-align: top; }}
th {{ background: #1e293b; text-align: left; }}
tr.ok td:nth-child(3) {{ color: #22c55e; font-weight: bold; }}
tr.fail td:nth-child(3) {{ color: #ef4444; font-weight: bold; }}
.summary {{ margin: 16px 0; padding: 12px; background: #1e293b; border-radius: 8px; }}
</style></head><body>
<h1>E2E комплектов (5 брендов по акциям)</h1>
<div class="summary"><b>{passed}/{len(results)}</b> пройдено ·
{sum(r.elapsed_s for r in results):.0f}s суммарно ·
LLM {html_lib.escape(settings.llm_provider)}</div>
<table>
<thead><tr><th>#</th><th>Запрос</th><th>OK</th><th>Бренд</th><th>Покрытие</th>
<th>Акция</th><th>Сумма − выгода = итог</th><th>Подарок</th><th>PDF</th>
<th>Время</th><th>Примечание</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</body></html>"""


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", type=str, default="", help="путь к HTML-отчёту")
    parser.add_argument("--json", type=str, default="", help="путь к JSON")
    args = parser.parse_args()

    settings = get_settings()
    pipeline = Pipeline(settings)
    results: list[CaseResult] = []
    print(f"Запуск {len(CASES)} комплектных кейсов (боевой поток)…\n", flush=True)
    for case in CASES:
        print(f"  >> {case.query!r} …", flush=True)
        r = await run_case(pipeline, case)
        results.append(r)
        mark = "OK" if r.ok else "FAIL"
        line = f"     [{mark}] {r.brand or '—'} {r.coverage}/{r.specs} за {r.elapsed_s}s"
        if r.campaign:
            line += f" | {r.campaign} выгода {r.benefit:,.0f}".replace(",", " ")
        print(line, flush=True)
        if r.notes or r.error:
            print(f"     note: {'; '.join(r.notes) or r.error}", flush=True)

    passed = sum(1 for r in results if r.ok)
    print(f"\nИтого: {passed}/{len(results)} OK", flush=True)

    if args.json:
        Path(args.json).write_text(
            json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    html_path = args.html or str(settings.output_path / "kit_e2e_report.html")
    Path(html_path).write_text(_html_report(results, settings), encoding="utf-8")
    print(f"HTML: {html_path}", flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
