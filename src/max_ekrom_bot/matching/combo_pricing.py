"""Расчёт итога комплекта по механике акции бренда (Ф2 проекта комплектов).

Вход — уже выбранный набор товаров одного бренда (комплект собирает Ф4) и кампания
из датасета акций (`ekrom/promos.py`, таблица `promo_campaigns`). Выход — разбивка
итога: подытог, скидка, финальная сумма и КАК скидка ложится (единый % на комплект
или на конкретный товар), плюс честная пометка, если акцию считать нельзя.

Почему считаем сами: живой тест показал, что корзина ekrom комплектную скидку НЕ
применяет — менеджер ставит её вручную «Индивидуальной скидкой». Механику берём из
правил кампании (распознаны на Ф1), а не с сайта.

Механики:
  • cascade_cheapest — % на НАИМЕНЬШИЙ по стоимости товар, % растёт с числом позиций;
  • flat_combo       — плоский % на весь подытог, % растёт с числом позиций;
  • gift/spec_price/unknown — авто-итог не гарантирован → manager_review=True;
  • нет кампании (напр. Lex) — скидки нет, итог = сумма цен.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ComboItem:
    """Позиция комплекта для расчёта (цена + товарная категория)."""

    title: str
    price: float
    category: str            # товарная категория (для правила «разных категорий»)
    is_oven: bool = False    # духовой шкаф (некоторые акции требуют его в комплекте)
    url: str | None = None


@dataclass
class ComboResult:
    """Итог расчёта комплекта: суммы + как именно ложится скидка."""

    mechanic: str
    subtotal: float
    discount_total: float
    final_total: float
    applied: bool = False                       # применена ли комплектная скидка
    combo_discount_pct: float | None = None     # flat_combo: единый % на подытог
    item_discounts: dict[int, float] = field(default_factory=dict)  # индекс позиции → % (каскад)
    note: str = ""                              # пояснение (условие/уточнение менеджера)
    manager_review: bool = False                # True → авто-итогу нельзя доверять как финалу


# Механики, чей итог автоматически считать нельзя — их размечаем на менеджера.
_MANAGER_REVIEW = {"gift", "spec_price", "unknown", "showcase"}


def _tier_for(tiers: dict[int, float], count: int) -> float | None:
    """Процент для комплекта из `count` позиций: наибольший тариф с ключом ≤ count.

    Так покрываем и «ровно N», и превышение верхнего тарифа (напр. 6 позиций при
    сетке до 5 → берём тариф пятёрки). Ниже минимального тарифа — None (нет скидки).
    """
    keys = [k for k in tiers if k <= count]
    return tiers[max(keys)] if keys else None


def compute_combo(items: list[ComboItem], campaign: object | None) -> ComboResult:
    """Посчитать итог комплекта под кампанию (или без неё).

    `campaign` — объект с полями `mechanic`, `tiers` (dict {кол-во: %}),
    `requires_oven` (bool); подойдёт и `PromoCampaign`, и строка из `PromoStore`.
    None — акции нет (Lex): итог = сумма цен.
    """
    subtotal = round(sum(i.price for i in items), 2)
    base = ComboResult(
        mechanic="none", subtotal=subtotal, discount_total=0.0, final_total=subtotal
    )
    if campaign is None or not items:
        base.note = "Комплектная скидка не действует — цена по прайсу." if items else ""
        return base

    mechanic = _get(campaign, "mechanic", "unknown")
    tiers = _get(campaign, "tiers", {}) or {}
    requires_oven = bool(_get(campaign, "requires_oven", False))
    base.mechanic = mechanic

    # Механики, которые бот честно не считает — итог за менеджером.
    if mechanic in _MANAGER_REVIEW:
        base.manager_review = True
        base.note = "Точный итог по этой акции уточнит менеджер."
        return base

    # Комплектные скидки требуют РАЗНЫХ товарных категорий — считаем по их числу.
    distinct = len({i.category for i in items})
    if requires_oven and not any(i.is_oven for i in items):
        base.note = "Для скидки по акции в комплекте нужен духовой шкаф."
        return base

    pct = _tier_for(tiers, distinct)
    if not pct:
        min_n = min(tiers) if tiers else 2
        base.note = f"Скидка по акции — от {min_n} позиций из разных категорий."
        return base

    if mechanic == "cascade_cheapest":
        # Скидка на НАИМЕНЬШИЙ по стоимости товар.
        idx = min(range(len(items)), key=lambda k: items[k].price)
        discount = round(items[idx].price * pct / 100, 2)
        return ComboResult(
            mechanic=mechanic, subtotal=subtotal, discount_total=discount,
            final_total=round(subtotal - discount, 2), applied=True,
            item_discounts={idx: pct},
            note=f"Скидка −{_pct(pct)}% на самый недорогой товар в комплекте.",
        )

    if mechanic == "flat_combo":
        discount = round(subtotal * pct / 100, 2)
        return ComboResult(
            mechanic=mechanic, subtotal=subtotal, discount_total=discount,
            final_total=round(subtotal - discount, 2), applied=True,
            combo_discount_pct=pct,
            note=f"Скидка −{_pct(pct)}% на комплект из {distinct} позиций.",
        )

    # На всякий случай: незнакомая механика — на менеджера.
    base.manager_review = True
    base.note = "Точный итог по этой акции уточнит менеджер."
    return base


def _pct(v: float) -> str:
    """Процент без хвостовых нулей (25.0 → «25», 12.5 → «12.5»)."""
    return f"{v:g}"


def _get(obj: object, name: str, default: object) -> object:
    """Достать поле и из объекта (PromoCampaign), и из dict-строки (PromoStore)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
