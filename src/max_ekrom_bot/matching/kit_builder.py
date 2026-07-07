"""Движок сборки комплекта одного бренда (Ф4 проекта комплектов).

Клиент называет позиции («духовка + варочная + вытяжка»), а бот собирает комплект
из ОДНОГО бренда пяти брендов проекта и в СОЧЕТАЮЩИХСЯ цветах: по каждой позиции
берёт лучший товар бренда, чей цвет входит в одну цветовую ГРУППУ (тёмная+сталь /
светлая+сталь — см. `_COLOR_GROUPS`), считает выгоду акции через
`combo_pricing.best_combo` и выбирает бренд с лучшим сочетанием покрытие → выгода →
цена. Так комплект визуально согласован (нержавейка/серый — нейтральные мостики,
сочетаются и с тёмным, и со светлым), но тёмное со светлым не смешивается. Политика
«нет идеального → ближайшее»: бренд/группа с неполным покрытием тоже рассматривается,
но покрытие приоритетнее всего.

Ядро чистое: на вход — уже собранные кандидаты по позициям (из каталога/кэша),
поставщик кампаний бренда и оцифровщик подарка. Интеграцию с реальным каталогом,
браузером и мессенджером делает вызывающий слой (pipeline/telegram).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import ApplianceType, CatalogProduct, MatchedItem, ProductSpec, SelectionResult
from .combo_pricing import ComboItem, ComboResult, best_combo
from .selector import ProductSelector, _COLOR_FAMILY, _product_colors

# Пять брендов проекта (дублируем как строки, чтобы не тянуть сетевой модуль promos).
TARGET_BRANDS: tuple[str, ...] = ("Korting", "Maunfeld", "Midea", "Lex", "Kuppersberg")


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
        )
        for m in matched
    ]
    combo = best_combo(combo_items, campaigns, gift_value)
    if colored and matched:
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
    """Собрать комплект бренда СТРОГО одного цвета.

    По каждой позиции ранжируем товары бренда, затем для каждого доступного цвета
    (канонический — «чёрный»/«белый»/«нержавейка»/…) собираем набор, где ВСЕ позиции
    этого цвета, и берём лучший по покрытие→выгода→цена. Так комплект всегда единого
    цвета/финиша. Если цвет ни у кого не распознан — собираем без цветовой гарантии
    (с пометкой, что цвет уточнит менеджер).

    candidates_per_spec[i] — кандидаты под specs[i] (любых брендов); отбираем товары
    `brand`. campaigns — акции бренда. gift_value — оцифровщик подарка (или None).
    """
    selector = selector or ProductSelector()
    ranked_per_spec: list[list[CatalogProduct]] = []
    for spec, cands in zip(specs, candidates_per_spec):
        brand_cands = [c for c in cands if _brand_of(c, brand)]
        ranked_per_spec.append(selector.ranked_candidates(spec, brand_cands))

    def assemble(group: frozenset[str] | None) -> list[MatchedItem]:
        items: list[MatchedItem] = []
        for spec, ranked in zip(specs, ranked_per_spec):
            if group is None:
                chosen = ranked[0] if ranked else None
            else:
                chosen = next((p for p in ranked if _product_colors(p) & group), None)
            miss = (f"У бренда {brand} нет позиции"
                    + (" в этой цветовой гамме" if group else "") + " под этот пункт")
            items.append(MatchedItem(
                spec=spec, product=chosen, candidates_count=len(ranked),
                reason="" if chosen else miss,
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
