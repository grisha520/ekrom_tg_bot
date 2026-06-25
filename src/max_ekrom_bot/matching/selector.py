"""Детерминированный подбор товара под ProductSpec.

Логика предсказуемая (без LLM) и состоит из двух частей:

1) Жёсткая фильтрация:
   - исключённые цвета («не чёрную») — отсекаем;
   - ПОЗИТИВНАЯ валидация (#1): если в названии товара явно заявлена
     характеристика, ПРОТИВОРЕЧАЩАЯ запросу (другой цвет / топливо / число
     конфорок), кандидат отбрасывается. Молчание ≠ противоречие: если признак в
     названии не упомянут — кандидат остаётся.

2) Взвешенный скоринг (#5): среди прошедших фильтр ранжируем по сумме сигналов
   `W_ATTR·(совпавшие_характеристики/запрошенные) + W_PRICE·попадание_в_сегмент`.
   Совпадение характеристик весит больше цены — лучше отдать правильный по
   параметрам товар, а цена решает уже среди равных по совпадению.
"""

from __future__ import annotations

import re
import statistics

from ..models import CatalogProduct, MatchedItem, ProductSpec, SelectionResult

# Веса скоринга: характеристики доминируют, цена — тай-брейк внутри сегмента.
_W_ATTR = 10.0
_W_PRICE = 1.0

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

# Семьи цветов: оттенки одной семьи не конфликтуют между собой. Нужно, чтобы под
# запрос «чёрный» подходили тёмные варианты (графит/антрацит/чёрное стекло), но
# отсекался явно другой цвет (белый/бежевый). Цвет, которого здесь нет, образует
# собственную «семью» (сам с собой).
_COLOR_FAMILY: dict[str, str] = {
    "чёрный": "тёмный",
    "чёрный матовый": "тёмный",
    "графит": "тёмный",
    "антрацит": "тёмный",
    "серый": "тёмный",
    "белый": "светлый",
    "бежевый": "светлый",
    "нержавейка": "сталь",
}

# Синонимы цвета → канонический ключ из _COLOR_TOKENS (LLM присылает по-разному).
_CANON_COLOR: dict[str, str] = {
    "нержавеющая сталь": "нержавейка",
    "нержавеющая": "нержавейка",
    "нержавеющий": "нержавейка",
    "инокс": "нержавейка",
    "стальной": "нержавейка",
    "стальная": "нержавейка",
}

# Топливо/тип нагрева: «электрическая» и «индукционная» считаем одной семьёй —
# конфликтом только газ-vs-электросемья (а индукция/электро — мягкое отличие,
# не повод выбрасывать кандидата).
_ELECTRIC_FAMILY = {"электрическая", "индукционная"}

# Нормализация топлива по стему: LLM присылает форму, согласованную с техникой
# («панель газовая», «шкаф газовый»). Без этого подбор по топливу ломается на
# роде/склонении — тот же класс бага, что был с цветом.
_FUEL_STEMS: tuple[tuple[str, str], ...] = (
    ("индукц", "индукционная"),
    ("газ", "газовая"),
    ("электр", "электрическая"),
    ("комбинир", "комбинированная"),
)


def _canon_fuel(fuel: str) -> str:
    f = fuel.strip().lower()
    for stem, canon in _FUEL_STEMS:
        if stem in f:
            return canon
    return f


def _canon_color(color: str) -> str:
    """Привести цвет к каноническому ключу _COLOR_TOKENS.

    Терпимо к форме слова: LLM присылает цвет в роде, согласованном с техникой
    («вытяжка чёрная», «шкаф чёрный», «панель чёрная»). Нормализуем по стему
    («чёрн» → «чёрный»), иначе подбор по цвету ломается на роде/склонении.
    """
    c = color.strip().lower()
    if c in _CANON_COLOR:
        return _CANON_COLOR[c]
    if c in _COLOR_TOKENS:
        return c
    for canon, tokens in _COLOR_TOKENS.items():
        if any(sub in c for sub in tokens["sub"]):
            return canon
    return c


def _title_has_color(title: str, color: str) -> bool:
    """Есть ли в названии признак данного цвета (рус. подстрока или англ. код-токен)."""
    spec = _COLOR_TOKENS.get(_canon_color(color))
    if not spec:
        return color.lower() in title.lower()
    low = title.lower()
    if any(s in low for s in spec["sub"]):
        return True
    tokens = set(re.split(r"[^a-zа-я0-9]+", low))
    # короткие однобуквенные коды (b/w/x) не используем для исключения — слишком шумно
    return bool({c for c in spec["codes"] if len(c) >= 2} & tokens)


def _title_colors(title: str) -> set[str]:
    """Множество цветов, ЯВНО заявленных в названии товара."""
    return {color for color in _COLOR_TOKENS if _title_has_color(title, color)}


def _title_fuel_set(title: str) -> set[str]:
    """Типы нагрева, заявленные в названии (газовая/электрическая/индукционная)."""
    low = title.lower()
    found: set[str] = set()
    if "газ" in low:
        found.add("газовая")
    if "индукц" in low:
        found.add("индукционная")
    if "электр" in low:
        found.add("электрическая")
    return found


def _title_burners(title: str) -> int | None:
    m = re.search(r"(\d)\s*конфор", title.lower())
    return int(m.group(1)) if m else None


def _title_width_mm(title: str) -> int | None:
    low = title.lower()
    m = re.search(r"(\d{2,3})\s*см\b", low)
    if m:
        return int(m.group(1)) * 10
    for m in re.finditer(r"\b(\d{3})\b", low):
        v = int(m.group(1))
        if 400 <= v <= 1200:
            return v
    return None


def _color_conflict(want: str, have: set[str]) -> bool:
    """Конфликт цвета: в товаре заявлен цвет ДРУГОЙ семьи, чем запрошен.

    Оттенки одной семьи не конфликтуют (графит/антрацит/чёрный — «тёмные»), но
    явно другой цвет (белый под запрос чёрного) — конфликт. Молчание (have
    пусто) конфликтом не считается.
    """
    if not have or want in have:
        return False
    want_fam = _COLOR_FAMILY.get(want, want)
    have_fams = {_COLOR_FAMILY.get(c, c) for c in have}
    return want_fam not in have_fams


def _fuel_conflict(want: str, have: set[str]) -> bool:
    """Противоречит ли заявленное в названии топливо запрошенному.

    Конфликт — только газ-vs-электросемья. Внутри электросемьи
    (электрическая/индукционная) расхождение конфликтом не считаем.
    """
    if not have or want in have:
        return False
    if want in _ELECTRIC_FAMILY and have <= _ELECTRIC_FAMILY:
        return False
    return True


# ── Сигналы характеристик: название + структурированные атрибуты (#4) ─────────
# Атрибуты читаем по ключам, чтобы не путать «Тип управления: Электронное» с
# типом нагрева. Если атрибутов нет (карточка не обогащена) — падаем на название.
_FUEL_KEYS = ("тип духов", "вид вароч", "тип вароч", "тип нагрева", "тип плиты", "тип поверхност")


def _attr_get(product: CatalogProduct, *key_subs: str) -> str | None:
    """Значение первого атрибута, чьё имя содержит одну из подстрок-ключей."""
    for k, v in (product.attributes or {}).items():
        kl = k.lower()
        if any(s in kl for s in key_subs):
            return v
    return None


def _product_colors(product: CatalogProduct) -> set[str]:
    text = product.title
    cval = _attr_get(product, "цвет")
    if cval:
        text = f"{text} {cval}"
    return _title_colors(text)


def _product_fuels(product: CatalogProduct) -> set[str]:
    fval = _attr_get(product, *_FUEL_KEYS)
    return _title_fuel_set(fval) if fval else _title_fuel_set(product.title)


def _product_burners(product: CatalogProduct) -> int | None:
    bval = _attr_get(product, "конфор")
    if bval:
        m = re.search(r"\d+", bval)
        if m:
            return int(m.group())
    return _title_burners(product.title)


def _product_width(product: CatalogProduct) -> int | None:
    for sub in ("ширина встраивания", "ширина"):
        wval = _attr_get(product, sub)
        if wval:
            m = re.search(r"\d{2,4}", wval)
            if m:
                return int(m.group())
    return _title_width_mm(product.title)


class ProductSelector:
    def __init__(self, segment: str | None = None) -> None:
        # segment: 'эконом' → дешевле, 'премиум' → дороже, иначе → средний
        self._segment = (segment or "средний").lower()

    def select_one(self, spec: ProductSpec, candidates: list[CatalogProduct]) -> MatchedItem:
        ranked = self.ranked_candidates(spec, candidates)
        if not ranked:
            return MatchedItem(
                spec=spec,
                product=None,
                candidates_count=len(candidates),
                reason="Подходящих вариантов с ценой не найдено",
            )

        chosen = ranked[0]
        return MatchedItem(
            spec=spec,
            product=chosen,
            candidates_count=len(candidates),
            reason=self._explain(spec, chosen, ranked),
        )

    def ranked_candidates(
        self, spec: ProductSpec, candidates: list[CatalogProduct]
    ) -> list[CatalogProduct]:
        """Кандидаты, прошедшие фильтры, отсортированные по взвешенному скорингу."""
        filtered = [c for c in candidates if self._passes_filters(spec, c)]
        priced = [c for c in filtered if c.price is not None]
        if not priced:
            return []
        prices = sorted(c.price or 0.0 for c in priced)
        return sorted(
            priced, key=lambda c: self._score(spec, c, prices), reverse=True
        )

    def select_all(
        self, specs_with_candidates: list[tuple[ProductSpec, list[CatalogProduct]]]
    ) -> SelectionResult:
        items = [self.select_one(spec, cands) for spec, cands in specs_with_candidates]
        return SelectionResult(items=items)

    # ── Внутреннее ───────────────────────────────────────────────────────────
    def passes_filters(self, spec: ProductSpec, product: CatalogProduct) -> bool:
        """Публичная обёртка: применима для повторной валидации после обогащения."""
        return self._passes_filters(spec, product)

    def color_confirmed_by_title(self, spec: ProductSpec, product: CatalogProduct) -> bool:
        """Подтверждает ли НАЗВАНИЕ товара совместимый с запросом цвет.

        Нужно, чтобы не открывать страницу товара зря: если цвет уже виден в
        названии (напр. «...BLACK») и совместим с запросом — доверяем карточке.
        Если цвет не запрошен — True (проверять нечего). Если цвет в названии не
        распознан — False: страницу нужно открыть и проверить по характеристикам
        (иначе пропустим, например, белую модель с «BLANC» в названии).
        """
        if not spec.color:
            return True
        title_colors = _title_colors(product.title)
        if not title_colors:
            return False
        return not _color_conflict(_canon_color(spec.color), title_colors)

    def _passes_filters(self, spec: ProductSpec, product: CatalogProduct) -> bool:
        colors = _product_colors(product)

        # Жёсткие исключения по цвету («не чёрную»).
        for excluded in spec.exclude_colors:
            if excluded and _canon_color(excluded) in colors:
                return False

        # Позитивная валидация (#1): отбрасываем только при ЯВНОМ противоречии
        # (заявлен другой цвет/топливо/число конфорок). Молчание — ок. Сигналы
        # берутся из названия И характеристик товара (#4).
        if spec.color and _color_conflict(_canon_color(spec.color), colors):
            return False
        if spec.fuel_type and _fuel_conflict(_canon_fuel(spec.fuel_type), _product_fuels(product)):
            return False
        if spec.burners:
            b = _product_burners(product)
            if b is not None and b != spec.burners:
                return False
        return True

    def _attr_signals(self, spec: ProductSpec, product: CatalogProduct) -> tuple[int, int]:
        """(совпавшие, запрошенные) характеристики по названию + атрибутам."""
        requested = 0
        matched = 0
        if spec.color:
            requested += 1
            if _canon_color(spec.color) in _product_colors(product):
                matched += 1
        if spec.fuel_type:
            requested += 1
            if _canon_fuel(spec.fuel_type) in _product_fuels(product):
                matched += 1
        if spec.burners:
            requested += 1
            if _product_burners(product) == spec.burners:
                matched += 1
        if spec.width_mm:
            requested += 1
            w = _product_width(product)
            if w is not None and abs(w - spec.width_mm) <= 15:
                matched += 1
        return matched, requested

    def _price_fit(self, price: float, prices: list[float]) -> float:
        """Попадание цены в ценовую стратегию сегмента, нормировано в [0, 1]."""
        lo, hi = prices[0], prices[-1]
        if hi == lo:
            return 0.5
        if self._segment.startswith("эконом"):
            return (hi - price) / (hi - lo)
        if self._segment.startswith("премиум"):
            return (price - lo) / (hi - lo)
        med = statistics.median(prices)
        spread = max(med - lo, hi - med) or 1.0
        return 1.0 - abs(price - med) / spread

    def _score(self, spec: ProductSpec, product: CatalogProduct, prices: list[float]) -> float:
        matched, requested = self._attr_signals(spec, product)
        attr_norm = (matched / requested) if requested else 0.0
        price_fit = self._price_fit(product.price or 0.0, prices)
        return _W_ATTR * attr_norm + _W_PRICE * price_fit

    def _explain(
        self, spec: ProductSpec, chosen: CatalogProduct, priced: list[CatalogProduct]
    ) -> str:
        prices = sorted(p.price or 0.0 for p in priced)
        matched, requested = self._attr_signals(spec, chosen)
        attr_note = f"; характеристики {matched}/{requested}" if requested else ""
        return (
            f"Выбран «{self._segment}» вариант: {chosen.title} — {chosen.price:.0f} ₽ "
            f"(из {len(priced)} подходящих, диапазон {prices[0]:.0f}–{prices[-1]:.0f} ₽{attr_note})"
        )
