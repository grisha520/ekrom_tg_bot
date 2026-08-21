"""Парсер сообщений без внешнего API (правила + регулярки).

Подходит, когда сообщение структурное (нумерованный список позиций с
короткими характеристиками), как в типовых запросах на подбор. Не требует
ключей и работает офлайн. Для «грязных» формулировок можно переключиться на
LLM-парсер (LLM_PROVIDER=openai).
"""

from __future__ import annotations

import re

from ..models import ApplianceType, CustomerRequest, ProductSpec

# Ключевые слова → тип техники. Порядок важен: СВЧ проверяем раньше духовки,
# т.к. строка «СВЧ встроенная ... с духовкой» содержит «духов».
# Порядок важен: посудомойка проверяется раньше «панели»/«машины», иначе
# «посудомоечная машина» уедет в стиральную, а «п/м» — в микроволновку.
# Сокращения тут продублированы на случай, если текст пришёл в парсер без
# разворота (см. parsing/abbreviations.py) — например, из старых вызовов.
_APPLIANCE_KEYWORDS: list[tuple[ApplianceType, tuple[str, ...]]] = [
    (ApplianceType.DISHWASHER, ("посудомо", "пмм", "п/м", "п.м")),
    (ApplianceType.MICROWAVE, ("свч", "микровол")),
    (ApplianceType.HOOD, ("вытяж",)),
    (ApplianceType.OVEN, ("духов", "духовк", "д/ш")),
    (ApplianceType.COOKTOP, ("варочн", "конфор", "варка", "варки", "в/п", "панель")),
    (ApplianceType.FRIDGE, ("холодильн", "х/к")),
]

_COLOR_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("нержавейка", ("нерж",)),
    ("белый", ("бел",)),
    ("чёрный", ("чёрн", "черн")),
    ("графит", ("графит",)),
    ("бежевый", ("беж",)),
    ("антрацит", ("антрацит",)),
]


def _detect_appliance(text: str) -> ApplianceType:
    low = text.lower()
    for appliance, keys in _APPLIANCE_KEYWORDS:
        if any(k in low for k in keys):
            return appliance
    return ApplianceType.UNKNOWN


def _detect_colors(text: str) -> tuple[str | None, list[str]]:
    """Вернуть (желаемый цвет, исключённые цвета).

    «не чёрную» → попадает в исключённые; обычное упоминание → желаемый.
    """
    low = text.lower()
    wanted: str | None = None
    excluded: list[str] = []
    for color, keys in _COLOR_KEYWORDS:
        for k in keys:
            for m in re.finditer(re.escape(k), low):
                # Смотрим ~6 символов перед вхождением на наличие отрицания «не»
                prefix = low[max(0, m.start() - 6):m.start()]
                if re.search(r"\bне\s*$", prefix):
                    if color not in excluded:
                        excluded.append(color)
                elif wanted is None:
                    wanted = color
    # если цвет одновременно и желаемый, и исключённый — приоритет исключению
    if wanted in excluded:
        wanted = None
    return wanted, excluded


def _detect_width_mm(text: str) -> int | None:
    low = text.lower()
    # «60 см» / «45 см» → мм
    m = re.search(r"(\d{2,3})\s*см\b", low)
    if m:
        return int(m.group(1)) * 10
    # явные мм: 600 / 450 / 900 (двух-трёхзначные, кратные «типовым»)
    for m in re.finditer(r"\b(\d{3})\b", low):
        val = int(m.group(1))
        if 400 <= val <= 1200:
            return val
    return None


def _detect_fuel(text: str) -> str | None:
    low = text.lower()
    if "индукц" in low:
        return "индукционная"
    if "электр" in low:
        return "электрическая"
    if "газ" in low:
        return "газовая"
    return None


def _detect_install(text: str) -> str | None:
    low = text.lower()
    if "полновстр" in low or "полновстро" in low:
        return "полновстраиваемая"
    if "встра" in low or "встро" in low:
        return "встраиваемая"
    return None


def _detect_burners(text: str) -> int | None:
    m = re.search(r"(\d)\s*конфор", text.lower())
    return int(m.group(1)) if m else None


def _detect_segment(text: str) -> str | None:
    low = text.lower()
    if "эконом" in low:
        return "эконом"
    if "премиум" in low or "премиал" in low:
        return "премиум"
    if "средн" in low:
        return "средний"
    return None


def _split_items(message: str) -> list[str]:
    """Разбить сообщение на строки-позиции.

    Сначала пытаемся по нумерации (1. 2) и т.п.), иначе — по непустым строкам,
    отбрасывая явно вводные («добрый день», «можно подбор…»).
    """
    # Нумерованные пункты: «1.», «2)», «3 -» в начале строки
    numbered = re.findall(r"(?m)^\s*\d+[\.\)\-]\s*(.+)$", message)
    if numbered:
        return [s.strip() for s in numbered if s.strip()]

    lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
    skip = ("добрый день", "здравствуй", "привет", "можно подбор", "сегмент")
    return [ln for ln in lines if not any(ln.lower().startswith(s) for s in skip)]


class RuleBasedParser:
    """Парсер без LLM. Совместим по интерфейсу с RequestParser.parse()."""

    def parse(self, message: str) -> CustomerRequest:
        segment = _detect_segment(message)
        specs: list[ProductSpec] = []

        for raw in _split_items(message):
            appliance = _detect_appliance(raw)
            if appliance is ApplianceType.UNKNOWN:
                continue
            wanted_color, excluded = _detect_colors(raw)
            specs.append(
                ProductSpec(
                    appliance=appliance,
                    raw_text=raw,
                    width_mm=_detect_width_mm(raw),
                    color=wanted_color,
                    install_type=_detect_install(raw),
                    fuel_type=_detect_fuel(raw),
                    burners=_detect_burners(raw),
                    exclude_colors=excluded,
                )
            )

        return CustomerRequest(specs=specs, segment=segment, original_message=message)
