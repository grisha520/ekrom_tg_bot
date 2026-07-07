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
    """Итог расчёта комплекта: суммы + как именно ложится выгода.

    `discount_total` — это ВЫГОДА комплекта в рублях (для сравнения акций между
    собой): у скидочных механик = размер скидки (и `final_total = subtotal −
    discount_total`), у подарка = цена подарка (а `final_total = subtotal`, ведь
    подарок идёт СВЕРХУ бесплатно и сумму комплекта не уменьшает).
    """

    mechanic: str
    subtotal: float
    discount_total: float
    final_total: float
    applied: bool = False                       # применена ли акция (скидка/подарок)
    combo_discount_pct: float | None = None     # flat_combo: единый % на подытог
    item_discounts: dict[int, float] = field(default_factory=dict)  # индекс позиции → % (каскад)
    gift_title: str | None = None               # подарок: название (сумму НЕ уменьшает)
    note: str = ""                              # пояснение (условие/уточнение менеджера)
    manager_review: bool = False                # True → авто-итогу нельзя доверять как финалу
    campaign_slug: str | None = None            # какая акция дала этот результат


# Механики, чей итог автоматически считать нельзя — их размечаем на менеджера.
_MANAGER_REVIEW = {"gift", "spec_price", "unknown", "showcase"}


def _tier_for(tiers: dict[int, float], count: int) -> float | None:
    """Процент для комплекта из `count` позиций: наибольший тариф с ключом ≤ count.

    Так покрываем и «ровно N», и превышение верхнего тарифа (напр. 6 позиций при
    сетке до 5 → берём тариф пятёрки). Ниже минимального тарифа — None (нет скидки).
    """
    keys = [k for k in tiers if k <= count]
    return tiers[max(keys)] if keys else None


def compute_combo(
    items: list[ComboItem], campaign: object | None, gift_value: object | None = None
) -> ComboResult:
    """Посчитать итог комплекта под кампанию (или без неё).

    `campaign` — объект с полями `mechanic`, `tiers` (dict {кол-во: %}),
    `requires_oven` (bool), `slug`; подойдёт и `PromoCampaign`, и строка из
    `PromoStore`. None — акции нет (Lex): итог = сумма цен.

    `gift_value` — необязательный оцифровщик подарка: callable(campaign, items) →
    (название, цена) или None. Позволяет сравнивать подарочную акцию со скидочными
    по рублёвой выгоде (решение заказчика). Без него подарок → «уточнит менеджер».
    """
    subtotal = round(sum(i.price for i in items), 2)
    slug = _get(campaign, "slug", None) if campaign is not None else None
    base = ComboResult(
        mechanic="none", subtotal=subtotal, discount_total=0.0, final_total=subtotal,
        campaign_slug=slug,
    )
    if campaign is None or not items:
        base.note = "Комплектная скидка не действует — цена по прайсу." if items else ""
        return base

    mechanic = _get(campaign, "mechanic", "unknown")
    tiers = _get(campaign, "tiers", {}) or {}
    requires_oven = bool(_get(campaign, "requires_oven", False))
    base.mechanic = mechanic

    # Механики, которые численно не считаем как скидку — на менеджера. Исключение:
    # подарок можно оцифровать через gift_value (выгода = цена подарка, сверху бесплатно).
    if mechanic in _MANAGER_REVIEW:
        if mechanic == "gift" and gift_value is not None:
            g = gift_value(campaign, items)
            if g and g[1]:
                title, price = g[0], round(float(g[1]), 2)
                return ComboResult(
                    mechanic=mechanic, subtotal=subtotal, discount_total=price,
                    final_total=subtotal, applied=True, gift_title=title,
                    campaign_slug=slug,
                    note=f"В подарок: {title} (стоимость {_fmt(price)}) — бесплатно.",
                )
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
            item_discounts={idx: pct}, campaign_slug=slug,
            note=f"Скидка −{_pct(pct)}% на самый недорогой товар в комплекте.",
        )

    if mechanic == "flat_combo":
        discount = round(subtotal * pct / 100, 2)
        return ComboResult(
            mechanic=mechanic, subtotal=subtotal, discount_total=discount,
            final_total=round(subtotal - discount, 2), applied=True,
            combo_discount_pct=pct, campaign_slug=slug,
            note=f"Скидка −{_pct(pct)}% на комплект из {distinct} позиций.",
        )

    # На всякий случай: незнакомая механика — на менеджера.
    base.manager_review = True
    base.note = "Точный итог по этой акции уточнит менеджер."
    return base


def best_combo(
    items: list[ComboItem], campaigns: list, gift_value: object | None = None
) -> ComboResult:
    """Выбрать ВЫГОДНЕЙШУЮ акцию бренда для этого комплекта.

    Акции брендов НЕ суммируются (правила: «не пересекается с другими акциями бренда»),
    поэтому применяем ровно одну — с максимальной рублёвой выгодой (`discount_total`).
    Если ни одна не применяется численно, но есть подарочная/нестандартная — возвращаем
    её с пометкой «уточнит менеджер». Нет кампаний (Lex) — итог по прайсу.
    """
    if not campaigns:
        return compute_combo(items, None)
    results = [compute_combo(items, c, gift_value) for c in campaigns]
    applied = [r for r in results if r.applied]
    if applied:
        return max(applied, key=lambda r: r.discount_total)
    review = [r for r in results if r.manager_review]
    return review[0] if review else compute_combo(items, None)


def make_gift_valuer(catalog_lookup: object):
    """Собрать оцифровщик подарка для `compute_combo`/`best_combo`.

    `catalog_lookup(model) → (название, цена) | None` — поиск подарка в каталоге по
    ключу модели (напр. `CatalogCache.find_by_model`). Возвращает callable(campaign,
    items) → (название, цена) | None, пригодный как аргумент `gift_value`.

    Условие подарка берётся из `campaign.gift` (оцифровано на Ф1: sum/count + пороги
    → модель). Проверяем порог (сумма комплекта / число разных категорий) и, для
    акций с обязательным духовым шкафом, его наличие. Не выполнено/не нашли цену → None.
    """
    def valuer(campaign: object, items: list[ComboItem]):
        gift = _get(campaign, "gift", None)
        if not gift:
            return None
        cond = gift["condition"] if isinstance(gift, dict) else gift.condition
        opts = gift["options"] if isinstance(gift, dict) else gift.options
        if not opts:
            return None
        if bool(_get(campaign, "requires_oven", False)) and not any(i.is_oven for i in items):
            return None
        metric = (round(sum(i.price for i in items), 2) if cond == "sum"
                  else len({i.category for i in items}))
        eligible = [thr for thr in opts if metric >= thr]
        if not eligible:
            return None
        found = catalog_lookup(opts[max(eligible)])
        if not found or not found[1]:
            return None
        return found[0], float(found[1])

    return valuer


def _pct(v: float) -> str:
    """Процент без хвостовых нулей (25.0 → «25», 12.5 → «12.5»)."""
    return f"{v:g}"


def _fmt(v: float) -> str:
    """Сумма в рублях с пробелами-разделителями («6 372 ₽»)."""
    return f"{int(round(v)):,}".replace(",", " ") + " ₽"


def _get(obj: object, name: str, default: object) -> object:
    """Достать поле и из объекта (PromoCampaign), и из dict-строки (PromoStore)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
