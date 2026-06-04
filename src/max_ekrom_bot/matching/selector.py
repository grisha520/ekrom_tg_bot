"""Детерминированный подбор товара под ProductSpec.

Логика проста и предсказуема (без LLM):
1) отфильтровать кандидатов по жёстким критериям (исключённые цвета и т.п.);
2) среди подходящих выбрать вариант по ценовой стратегии (по умолчанию —
   «средний по цене», что соответствует запросу «средний сегмент / средние цены»).
"""

from __future__ import annotations

import re
import statistics

from ..models import CatalogProduct, MatchedItem, ProductSpec, SelectionResult

# Токены цвета в названиях товаров (рус. подстроки + англ. коды-суффиксы).
# Коды сверяются как отдельные токены названия, чтобы не ловить часть артикула.
_COLOR_TOKENS: dict[str, dict[str, set[str]]] = {
    "чёрный": {"sub": {"чёрн", "черн"}, "codes": {"bl", "black", "bk", "b"}},
    "белый": {"sub": {"бел"}, "codes": {"wh", "white", "w"}},
    "нержавейка": {"sub": {"нерж"}, "codes": {"ix", "inox", "x", "stainless", "steel"}},
    "графит": {"sub": {"графит"}, "codes": {"gr", "graphite"}},
    "бежевый": {"sub": {"беж"}, "codes": {"bg", "beige", "iv"}},
    "серый": {"sub": {"сер"}, "codes": {"gy", "grey", "gray"}},
}


def _title_has_color(title: str, color: str) -> bool:
    """Есть ли в названии признак данного цвета (рус. подстрока или англ. код-токен)."""
    spec = _COLOR_TOKENS.get(color.lower())
    if not spec:
        return color.lower() in title.lower()
    low = title.lower()
    if any(s in low for s in spec["sub"]):
        return True
    tokens = set(re.split(r"[^a-zа-я0-9]+", low))
    # короткие однобуквенные коды (b/w/x) не используем для исключения — слишком шумно
    return bool({c for c in spec["codes"] if len(c) >= 2} & tokens)


class ProductSelector:
    def __init__(self, segment: str | None = None) -> None:
        # segment: 'эконом' → дешевле, 'премиум' → дороже, иначе → средний
        self._segment = (segment or "средний").lower()

    def select_one(self, spec: ProductSpec, candidates: list[CatalogProduct]) -> MatchedItem:
        filtered = [c for c in candidates if self._passes_filters(spec, c)]
        priced = [c for c in filtered if c.price is not None]

        if not priced:
            return MatchedItem(
                spec=spec,
                product=None,
                candidates_count=len(candidates),
                reason="Подходящих вариантов с ценой не найдено",
            )

        chosen = self._pick_by_segment(priced)
        return MatchedItem(
            spec=spec,
            product=chosen,
            candidates_count=len(candidates),
            reason=self._explain(chosen, priced),
        )

    def select_all(self, specs_with_candidates: list[tuple[ProductSpec, list[CatalogProduct]]]) -> SelectionResult:
        items = [self.select_one(spec, cands) for spec, cands in specs_with_candidates]
        return SelectionResult(items=items)

    # ── Внутреннее ───────────────────────────────────────────────────────────
    def _passes_filters(self, spec: ProductSpec, product: CatalogProduct) -> bool:
        title = f"{product.title} {' '.join(product.attributes.values())}"
        # Жёсткие исключения по цвету («не чёрную») — отсекаем по названию.
        for excluded in spec.exclude_colors:
            if excluded and _title_has_color(title, excluded):
                return False
        return True

    def _pick_by_segment(self, priced: list[CatalogProduct]) -> CatalogProduct:
        ordered = sorted(priced, key=lambda p: p.price or 0.0)
        if self._segment.startswith("эконом"):
            return ordered[0]
        if self._segment.startswith("премиум"):
            return ordered[-1]
        # средний: вариант, ближайший к медиане цен
        median = statistics.median(p.price or 0.0 for p in ordered)
        return min(ordered, key=lambda p: abs((p.price or 0.0) - median))

    def _explain(self, chosen: CatalogProduct, priced: list[CatalogProduct]) -> str:
        prices = sorted(p.price or 0.0 for p in priced)
        return (
            f"Выбран «{self._segment}» вариант: {chosen.title} — {chosen.price:.0f} ₽ "
            f"(из {len(priced)} подходящих, диапазон {prices[0]:.0f}–{prices[-1]:.0f} ₽)"
        )
