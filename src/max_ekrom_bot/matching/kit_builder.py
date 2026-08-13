"""Движок сборки комплекта одного бренда (Ф4 проекта комплектов).

Клиент называет позиции («духовка + варочная + вытяжка»), а бот собирает комплект
из ОДНОГО бренда пяти брендов проекта, откалиброванный по оплаченным подборкам
менеджера:

  • ЦВЕТ: позиция с явным цветом клиента («духовка белая, варка чёрная») получает
    ровно его; позиции БЕЗ цвета собираются в одну цветовую ГРУППУ (тёмная+сталь /
    светлая+сталь — `_COLOR_GROUPS`; нержавейка и серый — нейтральные мостики).
  • АКЦИИ: выгоду считает `combo_pricing.best_combo`; для механики «холодильник по
    спеццене» движок сам предпочитает акционную модель холодильника (если клиент не
    задал свою и она проходит фильтры позиции).
  • ОХВАТ: только 5 брендов — позиции вне охвата (мойки/смесители/измельчители/
    фильтры, чужие бренды) отделяются `split_out_of_scope` и уходят менеджеру.
  • Выбор бренда: покрытие → дизайн → выгода → цена; «нет идеального → ближайшее»,
    полнота комплекта важнее размера скидки.

Ядро чистое: на вход — уже собранные кандидаты по позициям (из каталога/кэша),
поставщик кампаний бренда и оцифровщик подарка. Интеграцию с реальным каталогом,
браузером и мессенджером делает вызывающий слой (pipeline/telegram).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import ApplianceType, CatalogProduct, MatchedItem, ProductSpec, SelectionResult
from .combo_pricing import ComboItem, ComboResult, best_combo
from .selector import (
    ProductSelector, _COLOR_FAMILY, _product_colors, _product_install, _product_width,
)

# Пять брендов проекта (дублируем как строки, чтобы не тянуть сетевой модуль promos).
TARGET_BRANDS: tuple[str, ...] = ("Korting", "Maunfeld", "Midea", "Lex", "Kuppersberg")

# Позиции вне охвата бота (решение заказчика: только техника 5 брендов; мойки,
# смесители, измельчители, фильтры, сантехника — подбирает менеджер).
_OUT_OF_SCOPE_STEMS: tuple[str, ...] = (
    "мойк", "раковин", "смесител", "измельчител", "фильтр", "сантех",
)
_OUT_OF_SCOPE_URL_STEMS: tuple[str, ...] = (
    "moyki", "rakovin", "smesitel", "izmelchitel", "filtr", "santekh",
)


def split_out_of_scope(specs: list[ProductSpec]) -> tuple[list[ProductSpec], list[ProductSpec]]:
    """Разделить позиции запроса: (для сборки комплекта, для менеджера).

    Менеджеру уходят позиции вне техники 5 брендов: мойки/смесители/измельчители/
    фильтры/сантехника и позиции с ЧУЖИМ брендом (Smeg, Bosch, …) — их бот не
    подбирает, а честно передаёт с лидом.
    """
    in_scope: list[ProductSpec] = []
    manager: list[ProductSpec] = []
    for s in specs:
        txt = " ".join(x for x in (s.raw_text, s.category_name or "") if x).lower()
        url = (s.category_url or "").lower()
        brand = (s.brand or "").strip().lower()
        foreign_brand = bool(brand) and not any(b.lower() in brand for b in TARGET_BRANDS)

        # Известный тип техники (духовка/варочная/…/ПОСУДОМОЙКА) — это техника 5
        # брендов, а не сантехника. Стоп-слова применяем только к нераспознанным
        # позициям: иначе «посудо-МОЙКА» ложно ловится стоп-словом «мойк».
        known_appliance = s.appliance is not ApplianceType.UNKNOWN
        is_dishwasher = "посудомо" in txt or "посудомо" in url  # посудомоечная/посудомойка
        text_out = (
            not known_appliance and not is_dishwasher
            and (any(st in txt for st in _OUT_OF_SCOPE_STEMS)
                 or any(st in url for st in _OUT_OF_SCOPE_URL_STEMS))
        )
        (manager if (foreign_brand or text_out) else in_scope).append(s)
    return in_scope, manager


def client_brands(specs: list[ProductSpec], original_message: str = "") -> tuple[str, ...]:
    """Бренды из пятёрки, которые клиент явно назвал допустимыми (или все 5).

    Эталон: «можно Kuppersberg Maunfeld: …» → перебираем только их. Ищем упоминания
    и в брендах позиций, и в исходном сообщении.
    """
    blob = (original_message or "").lower() + " " + " ".join(
        (s.brand or "").lower() for s in specs
    )
    mentioned = tuple(b for b in TARGET_BRANDS if b.lower() in blob)
    return mentioned or TARGET_BRANDS


@dataclass
class BrandKit:
    """Комплект одного бренда: подобранные позиции + итог акции + оценка дизайна."""

    brand: str
    selection: SelectionResult
    combo: ComboResult
    requested: int
    coverage: int                       # сколько позиций удалось закрыть
    design_consistent: bool             # согласован ли комплект по цвету/финишу
    design_note: str = ""
    manager_specs: list[ProductSpec] = field(default_factory=list)  # позиции → менеджеру

    @property
    def is_full(self) -> bool:
        return self.coverage == self.requested and self.requested > 0

    @property
    def rank_key(self) -> tuple:
        """Ключ сравнения комплектов: покрытие → дизайн → выгода → дешевизна."""
        return (
            self.coverage,
            1 if self.design_consistent else 0,
            self.combo.discount_total,
            -self.combo.final_total,
        )


def _spec_category(spec: ProductSpec) -> str:
    """Ключ товарной категории позиции (для правила акции «разные категории»)."""
    if spec.appliance is not ApplianceType.UNKNOWN:
        return spec.appliance.value
    return (spec.category_url or spec.category_name or spec.raw_text or "?").strip().lower()


def _brand_of(product: CatalogProduct, brand: str) -> bool:
    """Товар относится к бренду (по названию или полю brand)."""
    b = brand.lower()
    return b in (product.title or "").lower() or b in (product.brand or "").lower()


def _preferred_promo_models(campaigns: list, positions: int = 0) -> list[str]:
    """Модели, которые выгодно ставить в комплект по акциям бренда.

    Спеццена (RBN 1761/1965 — фикс-цена при комплекте) и подарочные модели
    (MID45S100I — −100%, если она закрывает запрошенную позицию). Правила с
    линейкой «Эксклюзив» не предпочитаем — условие не проверить по каталогу.

    `positions` — сколько РАЗНЫХ товарных категорий в запросе клиента. Для
    подарков-лестниц («2 прибора → вытяжка, 3 → духовой, 4 → ПММ» у Korting)
    выдаётся ровно ОДИН подарок — по максимальному достижимому порогу, причём
    порог обязан выполняться ОПЛАЧЕННЫМИ позициями: подарочная модель займёт
    одну из позиций и обнулится, значит оплаченных останется positions−1 — по
    ним и целимся (движок расчёта проверяет то же условие). Пороги по СУММЕ
    покупки заранее не проверить (цены ещё не выбраны) — такие модели оставляем
    все, финальное условие всё равно проверит оцифровщик подарка.
    """
    models: list[str] = []

    def _add(m: str) -> None:
        if m and m not in models:
            models.append(m)

    for c in campaigns:
        spec = c.get("spec") if isinstance(c, dict) else getattr(c, "spec", None)
        if spec:
            tiers = spec.get("tiers") if isinstance(spec, dict) else spec.tiers
            line = spec.get("line_label") if isinstance(spec, dict) else spec.line_label
            if tiers and not line:
                for m in (spec.get("models") if isinstance(spec, dict) else spec.models) or []:
                    _add(m)
        gift = c.get("gift") if isinstance(c, dict) else getattr(c, "gift", None)
        if gift:
            cond = gift.get("condition") if isinstance(gift, dict) else gift.condition
            opts = (gift.get("options") if isinstance(gift, dict) else gift.options) or {}
            if cond == "count":
                reachable = [t for t in opts if t <= positions - 1]
                if reachable:
                    _add(opts[max(reachable)])
                continue
            for m in opts.values():
                _add(m)
    return models


# Группы сочетающихся цветов (канонические цвета из selector._COLOR_TOKENS).
# Нержавейка и серый — нейтральные «мостики»: входят в обе группы. Тёмное и
# светлое между собой не смешиваем. Правь этот список, чтобы менять сочетаемость.
_COLOR_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"чёрный", "графит", "серый", "нержавейка"}),   # тёмная гамма + сталь
    frozenset({"белый", "бежевый", "серый", "нержавейка"}),   # светлая гамма + сталь
)


def _palette_note(products: list[CatalogProduct]) -> str:
    """Пометка о цвете комплекта: единый цвет или перечень сочетающихся цветов."""
    colors: list[str] = []
    for p in products:
        for c in sorted(_product_colors(p)):
            if c not in colors:
                colors.append(c)
    if not colors:
        return "Цвет комплекта уточнит менеджер."
    if len(colors) == 1:
        return f"Единый цвет комплекта: {colors[0]}."
    return "Сочетающиеся цвета: " + ", ".join(colors) + "."


def _design_consistency(products: list[CatalogProduct]) -> tuple[bool, str]:
    """Оценить визуальную сочетаемость комплекта по цвету/финишу.

    Бренд уже один, поэтому дизайн-эвристика опирается на цвет: если у товаров с
    распознанным цветом он из ОДНОЙ цветовой семьи (тёмные / сталь / белый / …) —
    считаем комплект сочетающимся. Честно: это «визуально сочетается», а не «одна
    линейка» (данных о коллекции на сайте почти нет).
    """
    families: set[str] = set()
    known = 0
    for p in products:
        colors = _product_colors(p)
        if not colors:
            continue
        known += 1
        families |= {_COLOR_FAMILY.get(c, c) for c in colors}
    if known == 0:
        return False, "Цвет комплекта уточните у менеджера."
    if len(families) <= 1:
        return True, "Комплект визуально сочетается (единый цвет/финиш)."
    return False, "В комплекте разные цвета — уточните сочетание у менеджера."


def _finalize_kit(
    brand: str, specs: list[ProductSpec], items: list[MatchedItem],
    campaigns: list, gift_value: object | None, *, colored: bool,
) -> BrandKit:
    """Досчитать комплект по готовому набору позиций: выгода акции + пометка цвета.

    colored=True — набор собран по цветовой группе (цвета согласованы), пометку
    строим из фактических цветов; иначе — мягкая эвристика _design_consistency.
    """
    selection = SelectionResult(items=items)
    matched = selection.matched
    combo_items = [
        ComboItem(
            title=m.product.title, price=m.product.price or 0.0,
            category=_spec_category(m.spec),
            is_oven=(m.spec.appliance is ApplianceType.OVEN),
            url=m.product.url,
            # Признаки для правил «не участвуют» (домино/отдельностоящие/…).
            width_mm=_product_width(m.product),
            install=_product_install(m.product),
        )
        for m in matched
    ]
    combo = best_combo(combo_items, campaigns, gift_value)
    if colored and matched:
        # Явные цвета клиента могут «нарушать» группу (белая духовка + чёрная
        # варка по запросу) — это НЕ рассогласование, а выполнение требований.
        # Если ХОТЬ ОДНА позиция с заданным цветом — пометка «по вашему запросу»
        # (иначе она теряла бы заданные цвета и вводила в заблуждение).
        if any(m.spec.color for m in matched):
            consistent, note = True, "Цвета позиций — по вашему запросу."
        else:
            consistent, note = True, _palette_note([m.product for m in matched])
    else:
        consistent, note = _design_consistency([m.product for m in matched])
    return BrandKit(
        brand=brand, selection=selection, combo=combo,
        requested=len(specs), coverage=len(matched),
        design_consistent=consistent, design_note=note,
    )


def build_brand_kit(
    brand: str,
    specs: list[ProductSpec],
    candidates_per_spec: list[list[CatalogProduct]],
    campaigns: list,
    *,
    selector: ProductSelector | None = None,
    gift_value: object | None = None,
) -> BrandKit:
    """Собрать комплект бренда в согласованных цветах, с приоритетом акционных моделей.

    По каждой позиции ранжируем товары бренда; позиции с ЯВНЫМ цветом клиента берут
    лучший товар этого цвета (селектор уже отфильтровал), позиции без цвета
    собираются в одну цветовую группу — перебираем группы и берём лучший набор по
    покрытие→выгода→цена. Если у бренда есть акция «холодильник по спеццене», для
    позиции-холодильника без явной модели клиента предпочитаем акционную модель.
    Цвет не распознан ни у кого — сборка без цветовой гарантии (пометка менеджеру).

    candidates_per_spec[i] — кандидаты под specs[i] (любых брендов); отбираем товары
    `brand`. campaigns — акции бренда. gift_value — оцифровщик подарка (или None).
    """
    selector = selector or ProductSelector()
    ranked_per_spec: list[list[CatalogProduct]] = []
    for spec, cands in zip(specs, candidates_per_spec):
        brand_cands = [c for c in cands if _brand_of(c, brand)]
        ranked_per_spec.append(selector.ranked_candidates(spec, brand_cands))

    # Порог подарка считается по РАЗНЫМ товарным категориям — так же, как в
    # оцифровщике подарка (make_gift_valuer), иначе предпочли бы недостижимую модель.
    promo_models = _preferred_promo_models(campaigns, len({_spec_category(s) for s in specs}))

    def pick(spec: ProductSpec, ranked: list[CatalogProduct],
             group: frozenset[str] | None) -> CatalogProduct | None:
        # Явный цвет клиента: селектор уже отфильтровал — цветовая группа не давит.
        if group is None or spec.color:
            pool = ranked
        else:
            pool = [p for p in ranked if _product_colors(p) & group]
        if not pool:
            return None
        # Акционные модели (спеццена холодильника / подарочная ПММ): предпочитаем,
        # если позиция без явной модели клиента — они дают фикс-цену или −100%.
        if promo_models and not spec.model:
            hot = next(
                (p for p in pool
                 if any(m.lower() in p.title.lower() for m in promo_models)),
                None,
            )
            if hot is not None:
                return hot
        return pool[0]

    def assemble(group: frozenset[str] | None) -> list[MatchedItem]:
        items: list[MatchedItem] = []
        for spec, ranked in zip(specs, ranked_per_spec):
            chosen = pick(spec, ranked, group)
            miss = (f"У бренда {brand} нет позиции"
                    + (" в этой цветовой гамме" if group else "") + " под этот пункт")
            # Честная пометка о превышении бюджета клиента: у бренда не нашлось
            # варианта в потолке цены — замену показываем, но НЕ молча (e2e-прогон
            # поймал духовку за 40 990 на запрос «до 30000» без предупреждения).
            reason = ""
            if chosen and spec.max_price and (chosen.price or 0) > spec.max_price:
                reason = (f"Дороже бюджета (до {spec.max_price:,} ₽) — ближайший "
                          f"вариант бренда".replace(",", " "))
            items.append(MatchedItem(
                spec=spec, product=chosen, candidates_count=len(ranked),
                reason=reason if chosen else miss,
            ))
        return items

    # Для каждой цветовой ГРУППЫ — свой согласованный комплект; берём лучший.
    group_kits = [
        _finalize_kit(brand, specs, assemble(group), campaigns, gift_value, colored=True)
        for group in _COLOR_GROUPS
    ]
    group_kits = [k for k in group_kits if k.coverage > 0]
    if group_kits:
        return max(group_kits, key=lambda k: k.rank_key)

    # Цвет не распознан ни у одного товара — комплект без цветовой гарантии.
    return _finalize_kit(brand, specs, assemble(None), campaigns, gift_value, colored=False)


def choose_best_kit(
    specs: list[ProductSpec],
    candidates_per_spec: list[list[CatalogProduct]],
    campaigns_for_brand: object,
    *,
    brands: tuple[str, ...] = TARGET_BRANDS,
    selector: ProductSelector | None = None,
    gift_value: object | None = None,
) -> BrandKit | None:
    """Выбрать лучший бренд-комплект среди пяти брендов.

    campaigns_for_brand(brand) → список акций бренда (напр.
    `PromoStore.campaigns_for_brand`; для Lex вернёт []). Сравнение — по
    `BrandKit.rank_key` (покрытие → дизайн → выгода → цена). None — ни один бренд
    не закрыл ни одной позиции.
    """
    selector = selector or ProductSelector()
    kits: list[BrandKit] = []
    for brand in brands:
        campaigns = list(campaigns_for_brand(brand) or [])
        kit = build_brand_kit(
            brand, specs, candidates_per_spec, campaigns,
            selector=selector, gift_value=gift_value,
        )
        if kit.coverage > 0:
            kits.append(kit)
    if not kits:
        return None
    return max(kits, key=lambda k: k.rank_key)
