"""Расчёт итога комплекта по механике акции бренда (Ф2 проекта комплектов).

Вход — уже выбранный набор товаров одного бренда (комплект собирает Ф4) и кампания
из датасета акций (`ekrom/promos.py`, таблица `promo_campaigns`). Выход — разбивка
итога: подытог, скидка, финальная сумма и КАК скидка ложится (единый % на комплект
или на конкретный товар), плюс честная пометка, если акцию считать нельзя.

Почему считаем сами: живой тест показал, что корзина ekrom комплектную скидку НЕ
применяет — менеджер ставит её вручную «Индивидуальной скидкой». Механику берём из
правил кампании (распознаны на Ф1), а не с сайта.

Механики (сверены с оплаченными подборками менеджера):
  • cascade_cheapest — % на НАИМЕНЬШИЙ по стоимости товар, % растёт с числом позиций;
  • flat_combo       — плоский % на участвующие позиции, % растёт с их числом;
  • spec_price       — доп. предмет (холодильник конкретной модели) по фикс-цене
                       при комплекте из N предметов (Kuppersberg 3→40 000/4→27 000);
  • gift             — подарок: если модель подарка — одна из ЗАПРОШЕННЫХ позиций,
                       она обнуляется (−100%) и ВЫЧИТАЕТСЯ из итога (как в оплаченных
                       PDF); иначе подарок идёт сверху бесплатно (сумму не меняет);
  • unknown/showcase — авто-итог не гарантирован → manager_review=True;
  • нет кампании (напр. Lex) — скидки нет, итог = сумма цен.

Исключения кампаний («домино/отдельностоящие/соло-СВЧ/… не участвуют») выводят
позицию из счёта предметов и из базы скидки — как делает менеджер в эталонах.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit


@dataclass
class ComboItem:
    """Позиция комплекта для расчёта (цена + признаки для правил акций)."""

    title: str
    price: float
    category: str            # товарная категория (для правила «разных категорий»)
    is_oven: bool = False    # духовой шкаф (некоторые акции требуют его в комплекте)
    url: str | None = None
    width_mm: int | None = None      # ширина (домино <350 мм не участвует в акциях)
    install: str | None = None       # «встраиваемый» / «отдельностоящий» (для исключений)


@dataclass
class ComboResult:
    """Итог расчёта комплекта: суммы + как именно ложится выгода.

    `discount_total` — это ВЫГОДА комплекта в рублях (для сравнения акций между
    собой). У скидочных механик и подарка-внутри-комплекта `final_total = subtotal −
    discount_total`; у подарка СВЕРХУ (модель подарка не входит в запрошенные
    позиции) `final_total = subtotal` — подарок бесплатный и сумму не меняет.
    """

    mechanic: str
    subtotal: float
    discount_total: float
    final_total: float
    applied: bool = False                       # применена ли акция (скидка/подарок)
    combo_discount_pct: float | None = None     # flat_combo: единый % на подытог
    item_discounts: dict[int, float] = field(default_factory=dict)  # индекс позиции → % скидки
    gift_title: str | None = None               # подарок: название
    note: str = ""                              # пояснение (условие/уточнение менеджера)
    manager_review: bool = False                # True → авто-итогу нельзя доверять как финалу
    campaign_slug: str | None = None            # какая акция дала этот результат
    campaign_title: str | None = None           # её название (для показа клиенту)
    campaign_period_to: str | None = None       # ISO-дата окончания акции (или None)
    # Другие акции бренда, посчитанные для этого же комплекта: (название, выгода).
    # Нужны, чтобы объяснить клиенту выбор фактами, а не постфактум-рассуждением.
    alternatives: list[tuple[str, float]] = field(default_factory=list)


# Механики, чей итог автоматически считать нельзя — их размечаем на менеджера.
# (spec_price сюда попадает только если правило не распарсилось из условий.)
_MANAGER_REVIEW = {"gift", "spec_price", "unknown", "showcase"}


# Категория прибора по названию — для проверки «подарок дублирует покупку».
# Ключи совпадают со значениями ApplianceType, которыми заполняется
# ComboItem.category, поэтому сравнение прямое.
_CATEGORY_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dishwasher", ("посудомоечн", "посудомой")),
    ("microwave", ("микроволнов", "свч")),
    ("oven", ("духово", "духовк")),
    ("cooktop", ("варочн",)),
    ("hood", ("вытяжк",)),
    ("fridge", ("холодильник",)),
)


def category_of_title(title: str) -> str | None:
    """Категория прибора по названию товара («…посудомоечная машина…» → dishwasher).

    Порядок проверок важен: «встраиваемая посудомоечная машина» не должна
    попасть в oven из-за слова «встраиваемая», а СВЧ проверяем до духового
    шкафа — у комби-моделей в названии есть оба слова.
    """
    low = (title or "").lower()
    for key, words in _CATEGORY_WORDS:
        if any(w in low for w in words):
            return key
    return None


def _matches_model(item: ComboItem, models: list[str]) -> bool:
    """Совпадает ли позиция с одной из моделей правила (по подстроке названия)."""
    low = (item.title or "").lower()
    return any(m and m.lower() in low for m in models)


def _excluded(item: ComboItem, exclusions: list[str]) -> bool:
    """Выведена ли позиция из акции правилом «не участвуют» этой кампании."""
    low = (item.title or "").lower()
    cat = (item.category or "").lower()
    for key in exclusions:
        if key == "домино":
            is_cooktop = "cooktop" in cat or "вароч" in cat or "вароч" in low
            narrow = item.width_mm is not None and item.width_mm < 350
            if is_cooktop and (narrow or "домино" in low):
                return True
        elif key == "отдельностоящая":
            if (item.install or "") == "отдельностоящий":
                return True
        elif key == "соло-свч":
            is_mw = "microwave" in cat or "свч" in low or "микроволнов" in low
            if is_mw and (item.install or "") == "отдельностоящий":
                return True
        elif key == "измельчитель":
            if "измельчит" in low or "измельчит" in cat:
                return True
        elif key == "соло-кофемашина":
            if "кофемашин" in low and (item.install or "") == "отдельностоящий":
                return True
        # «мбт»: клиентские позиции комплекта — крупная техника; надёжного
        # признака мелкой бытовой техники в данных нет, не отсекаем.
    return False


def _url_path(url: str | None) -> str:
    """Путь товара из URL (без домена/хвостового слэша) — для сравнения ссылок.

    Витринные участники акции и товары каталога могут ссылаться на один товар
    через разные хосты (www / поддомен города) — сравниваем только путь.
    """
    try:
        return (urlsplit(url or "").path or "").rstrip("/").lower()
    except ValueError:
        return ""


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
        campaign_title=_get(campaign, "title", None) if campaign is not None else None,
        campaign_period_to=_get(campaign, "period_to", None) if campaign is not None else None,
    )
    if campaign is None or not items:
        base.note = "Комплектная скидка не действует — цена по прайсу." if items else ""
        return base

    mechanic = _get(campaign, "mechanic", "unknown")
    tiers = _get(campaign, "tiers", {}) or {}
    requires_oven = bool(_get(campaign, "requires_oven", False))
    exclusions = list(_get(campaign, "exclusions", []) or [])
    base.mechanic = mechanic

    # Позиции, участвующие в акции: вне участия — исключения кампании
    # («домино/отдельностоящие/… не участвуют») и, для акций «только на
    # перечисленные товары» (scope=listed), модели вне списка участников.
    # Только участвующие идут в счёт предметов и в базу скидки — как в эталонах.
    listed_paths: set[str] = set()
    if _get(campaign, "scope", None) == "listed":
        listed_paths = {p for p in
                        (_url_path(u) for u in (_get(campaign, "listed_urls", []) or []))
                        if p}

    def _participates(it: ComboItem) -> bool:
        if _excluded(it, exclusions):
            return False
        if listed_paths:
            return _url_path(it.url) in listed_paths
        return True

    part = [k for k, it in enumerate(items) if _participates(it)]
    if items and not part and listed_paths:
        base.note = ("В этой акции участвуют только отдельные модели — "
                     "позиции комплекта в неё не входят.")
        return base

    # «Доп. предмет по спеццене» (холодильник конкретной модели) — считается по
    # распарсенному правилу; без правила падаем в manager_review ниже.
    spec = _get(campaign, "spec", None)
    if mechanic == "spec_price" and spec:
        return _compute_spec_price(items, part, spec, base, requires_oven)

    # Механики, которые численно не считаем как скидку — на менеджера. Исключение:
    # подарок оцифровывается через gift_value (модель + цена из каталога).
    if mechanic in _MANAGER_REVIEW:
        if mechanic == "gift" and gift_value is not None:
            g = gift_value(campaign, items)
            if g and g[1]:
                title, price = g[0], round(float(g[1]), 2)
                model = g[2] if len(g) > 2 else None
                # Подарок — одна из ЗАПРОШЕННЫХ позиций? Тогда она обнуляется и
                # ВЫЧИТАЕТСЯ из итога (эталон: ПММ за 0/1 ₽, «Скидка −100%»).
                gidx = next(
                    (k for k, it in enumerate(items)
                     if model and model.lower() in (it.title or "").lower()),
                    None,
                )
                if gidx is not None:
                    # Порог обязан выполняться ОПЛАЧЕННЫМИ позициями — без самой
                    # подарочной (эталон Stefany: ПММ шла пятой при пороге 4;
                    # спеццена считает others так же). Валуер на остатке должен
                    # дать ТУ ЖЕ модель: у лестниц меньший порог дарит другое.
                    paid = [it for k, it in enumerate(items) if k != gidx]
                    g_paid = gift_value(campaign, paid)
                    if g_paid and len(g_paid) > 2 and g_paid[2] == model:
                        discount = round(items[gidx].price, 2)
                        # replace() сохраняет «паспорт» акции (slug/название/
                        # срок) — его показываем клиенту вместе с выгодой.
                        return replace(
                            base, discount_total=discount,
                            final_total=round(subtotal - discount, 2), applied=True,
                            gift_title=items[gidx].title, item_discounts={gidx: 100.0},
                            note=f"{items[gidx].title} — в подарок (скидка −100%).",
                        )
                    # Оплаченных не хватает на этот подарок — не обещаем −100%,
                    # честно отдаём менеджеру (клиент может добрать позицию).
                    base.manager_review = True
                    base.note = ("Подарок по акции требует больше оплаченных "
                                 "приборов — условия подтвердит менеджер.")
                    return base
                return replace(
                    base, discount_total=price,
                    final_total=subtotal, applied=True, gift_title=title,
                    note=f"В подарок: {title} (стоимость {_fmt(price)}) — бесплатно.",
                )
        base.manager_review = True
        base.note = "Точный итог по этой акции уточнит менеджер."
        return base

    # Комплектные скидки требуют РАЗНЫХ товарных категорий среди УЧАСТВУЮЩИХ позиций.
    distinct = len({items[k].category for k in part})
    if requires_oven and not any(i.is_oven for i in items):
        base.note = "Для скидки по акции в комплекте нужен духовой шкаф."
        return base

    pct = _tier_for(tiers, distinct)
    if not pct:
        min_n = min(tiers) if tiers else 2
        base.note = f"Скидка по акции — от {min_n} позиций из разных категорий."
        return base

    excl_note = ""
    if len(part) < len(items):
        excl_note = f" (без {len(items) - len(part)} поз. вне акции)"

    if mechanic == "cascade_cheapest":
        # Скидка на НАИМЕНЬШИЙ по стоимости УЧАСТВУЮЩИЙ товар.
        idx = min(part, key=lambda k: items[k].price)
        discount = round(items[idx].price * pct / 100, 2)
        return replace(
            base, discount_total=discount,
            final_total=round(subtotal - discount, 2), applied=True,
            item_discounts={idx: pct},
            note=f"Скидка −{_pct(pct)}% на самый недорогой товар в комплекте{excl_note}.",
        )

    if mechanic == "flat_combo":
        # Плоский % на УЧАСТВУЮЩИЕ позиции (эталон: домино в базу не входит).
        pbase = round(sum(items[k].price for k in part), 2)
        discount = round(pbase * pct / 100, 2)
        return replace(
            base, discount_total=discount,
            final_total=round(subtotal - discount, 2), applied=True,
            combo_discount_pct=pct,
            # На каждой участвующей позиции — её % (как в эталонных PDF менеджера:
            # «Скидка −11%» на каждой строке + общий итог).
            item_discounts={k: pct for k in part},
            note=f"Скидка −{_pct(pct)}% на комплект из {distinct} позиций{excl_note}.",
        )

    # На всякий случай: незнакомая механика — на менеджера.
    base.manager_review = True
    base.note = "Точный итог по этой акции уточнит менеджер."
    return base


def _compute_spec_price(
    items: list[ComboItem], part: list[int], spec: object, base: ComboResult,
    requires_oven: bool,
) -> ComboResult:
    """Механика «доп. предмет по спеццене»: холодильник модели X за фикс-цену.

    Правило Kuppersberg: комплект из N УЧАСТВУЮЩИХ предметов (духовой обязателен)
    → следующий предмет — холодильник RBN … — за фикс-цену; выгода = РРЦ − фикс.
    Вариант Korting (−pct% на модель) требует духовой из линейки «Эксклюзив» —
    принадлежность линейке по каталогу не проверить, поэтому честно отдаём менеджеру.
    """
    models = list(_get(spec, "models", []) or [])
    price_tiers = {int(k): float(v) for k, v in (_get(spec, "tiers", {}) or {}).items()}
    pct = _get(spec, "pct", None)
    line_label = _get(spec, "line_label", None)

    # Спец-предмет (холодильник) среди позиций комплекта.
    sidx = next((k for k, it in enumerate(items) if _matches_model(it, models)), None)
    if sidx is None:
        base.note = ("Для этой акции в комплекте нужен холодильник "
                     + " / ".join(models) + ".")
        return base

    if pct is not None and line_label:
        # Условие «духовой из линейки „Эксклюзив“» не проверить по данным каталога.
        base.manager_review = True
        base.note = (f"Возможна скидка −{_pct(float(pct))}% на холодильник "
                     f"{items[sidx].title}: нужен духовой шкаф линейки "
                     f"«{line_label}» — подтвердит менеджер.")
        return base

    if requires_oven and not any(i.is_oven for i in items):
        base.note = "Для спеццены холодильника в комплекте нужен духовой шкаф."
        return base

    # Число УЧАСТВУЮЩИХ предметов БЕЗ самого спец-предмета (он идёт N+1-м).
    others = [k for k in part if k != sidx]
    distinct = len({items[k].category for k in others})
    catalog_price = items[sidx].price

    if pct is not None:
        discount = round(catalog_price * float(pct) / 100, 2)
        n_required = 1
        ok = distinct >= n_required
        fixed = catalog_price - discount
    else:
        eligible = [n for n in price_tiers if distinct >= n]
        ok = bool(eligible)
        fixed = price_tiers[max(eligible)] if eligible else None
        discount = round(catalog_price - fixed, 2) if fixed is not None else 0.0

    if not ok:
        min_n = min(price_tiers) if price_tiers else 1
        base.note = (f"Спеццена холодильника — при комплекте от {min_n} предметов "
                     "разных категорий (без учёта позиций вне акции).")
        return base
    if discount <= 0:
        base.note = "Спеццена не даёт выгоды против текущей цены холодильника."
        return base

    return replace(
        base, mechanic="spec_price", discount_total=discount,
        final_total=round(base.subtotal - discount, 2), applied=True,
        item_discounts={sidx: round(discount / catalog_price * 100, 2)},
        note=(f"{items[sidx].title} — по спеццене {_fmt(fixed)} "
              f"при комплекте из {distinct} предметов."),
    )


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
        best = max(applied, key=lambda r: r.discount_total)
    else:
        review = [r for r in results if r.manager_review]
        best = review[0] if review else compute_combo(items, None)
    # Проигравшие варианты запоминаем: из них строится объяснение выбора клиенту.
    best.alternatives = [
        (r.campaign_title or r.campaign_slug or "", r.discount_total)
        for r in results
        if r is not best and r.applied and (r.campaign_title or r.campaign_slug)
    ]
    return best


def make_gift_valuer(catalog_lookup: object):
    """Собрать оцифровщик подарка для `compute_combo`/`best_combo`.

    `catalog_lookup(model) → (название, цена) | None` — поиск подарка в каталоге по
    ключу модели (напр. `CatalogCache.find_by_model`). Возвращает callable(campaign,
    items) → (название, цена, ключ_модели) | None, пригодный как аргумент `gift_value`.
    Ключ модели нужен движку, чтобы понять: подарок — одна из позиций комплекта
    (тогда −100% и вычет) или идёт сверху бесплатно.

    Условие подарка берётся из `campaign.gift` (оцифровано на Ф1: sum/count + пороги
    → модель). Проверяем порог (сумма комплекта / число разных категорий) и, для
    акций с обязательным духовым шкафом, его наличие. Не выполнено/не нашли цену → None.

    Порог считается по УЧАСТВУЮЩИМ позициям: исключённые правилами кампании
    (домино/соло-СВЧ/…) не приближают подарок ни числом, ни суммой — иначе бот
    обещал бы подарок, на который клиент не наработал.
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
        exclusions = list(_get(campaign, "exclusions", []) or [])
        part = [i for i in items if not _excluded(i, exclusions)]
        metric = (round(sum(i.price for i in part), 2) if cond == "sum"
                  else len({i.category for i in part}))
        eligible = [thr for thr in opts if metric >= thr]
        if not eligible:
            return None
        # Идём от максимального достижимого порога ВНИЗ: если подарок верхнего
        # порога дублирует уже купленную категорию, пробуем следующий — лучше
        # отдать клиенту меньший, но нужный прибор, чем ничего.
        for thr in sorted(eligible, reverse=True):
            model = opts[thr]
            found = catalog_lookup(model)
            if not found or not found[1]:
                continue
            title = found[0]
            # Подарок СВЕРХУ не должен дублировать категорию, которую клиент и
            # так покупает: «в подарок посудомойка» к уже выбранной посудомойке —
            # вторая ненужная машина, а её цена раздувала выгоду и уводила выбор
            # бренда. Если подарочная модель САМА стоит в комплекте — это другой
            # случай (позиция обнуляется), он разрешён.
            in_kit = any(model.lower() in (i.title or "").lower() for i in items)
            if not in_kit:
                gift_cat = category_of_title(title)
                if gift_cat and any(i.category == gift_cat for i in items):
                    continue
            return title, float(found[1]), model
        return None

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
