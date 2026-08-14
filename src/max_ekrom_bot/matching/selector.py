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

from ..models import ApplianceType, CatalogProduct, MatchedItem, ProductSpec, SelectionResult

# Веса скоринга: характеристики доминируют, цена — тай-брейк внутри сегмента.
_W_ATTR = 10.0
_W_PRICE = 1.0

# Токены цвета в названиях товаров (рус. подстроки + англ. коды-суффиксы).
# Коды сверяются как отдельные токены названия, чтобы не ловить часть артикула.
_COLOR_TOKENS: dict[str, dict[str, set[str]]] = {
    "чёрный": {"sub": {"чёрн", "черн"}, "codes": {"bl", "black", "bk", "b"}},
    "белый": {"sub": {"бел"}, "codes": {"wh", "white", "w"}},
    "нержавейка": {"sub": {"нерж", "серебр"}, "codes": {"ix", "inox", "x", "stainless", "steel"}},
    "графит": {"sub": {"графит"}, "codes": {"gr", "graphite"}},
    # Слоновая кость / айвори / кремовый / шампань — кремовые оттенки, ближе к
    # бежевому: под запрос «белый» они НЕ должны подходить.
    "бежевый": {
        "sub": {"беж", "слонов", "айвори", "кремов", "шампан", "ваниль"},
        "codes": {"bg", "beige", "iv", "ivory", "champ"},
    },
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
    "нержавейка": "сталь",
    # белый и бежевый — каждый сам по себе (НЕ взаимозаменяемы: «бежевая» не
    # должна подходить под «белый» и наоборот).
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


# Взаимоисключающие подтипы товара (по стемам). Категории ekrom смешивают близкие
# типы (в «Кофемашинах» треть — кофеварки, в «Морозильных камерах» — лари), и
# «средний» по цене выбор попадал не в тот подтип. Если клиент явно назвал один
# подтип пары, товары с противоположным подтипом в названии/атрибутах
# отсеиваются. Молчание (нет ни того ни другого стема) — не конфликт.
_SUBTYPE_PAIRS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"кофемашин", "автомат"}), frozenset({"кофеварк", "капельн"})),
    (frozenset({"камер"}), frozenset({"ларь", "лари", "ларе"})),
    (frozenset({"зернов"}), frozenset({"капсульн"})),
    (frozenset({"встраиваем", "встроен"}), frozenset({"отдельностоящ"})),
    (frozenset({"side-by-side", "side by side", "двухдвер"}), frozenset({"однокамер"})),
)

# Допуск по ширине при жёстком фильтре (мм): 60 см = 600, 59.5 см тоже ок.
_WIDTH_TOLERANCE_MM = 10

# Ключи атрибутов, по которым определяем подтип (тип/вид товара на карточке).
_SUBTYPE_ATTR_KEY_SUBS = ("тип", "вид")


def _has_stem(text: str, stems: frozenset[str]) -> bool:
    return any(s in text for s in stems)


def _is_combi_microwave_oven(product: CatalogProduct) -> bool:
    """Духовой шкаф с функцией СВЧ (комби 2-в-1) — по названию или атрибуту.

    Такие дороже обычных и дублируют отдельную микроволновку, поэтому под простой
    «духовой шкаф» их не подставляем (если клиент не просил СВЧ).
    """
    low = product.title.lower()
    if "свч" in low or "микроволн" in low:
        return True
    val = _attr_get(product, "функция свч", "функция микроволнов")
    return bool(val) and "да" in val.lower()


def _request_has_subtype_intent(request_text: str) -> bool:
    req = (request_text or "").lower()
    for a, b in _SUBTYPE_PAIRS:
        if _has_stem(req, a) or _has_stem(req, b):
            return True
    return False


def _product_subtype_text(product: CatalogProduct) -> str:
    """Текст для определения подтипа: название + тип/вид из характеристик."""
    parts = [product.title]
    for k, v in (product.attributes or {}).items():
        kl = k.lower()
        if any(s in kl for s in _SUBTYPE_ATTR_KEY_SUBS):
            parts.append(v)
    return " ".join(parts).lower()


def _subtype_conflict(request_text: str, product_text: str) -> bool:
    """Назван ли в запросе один подтип пары, а у товара — противоположный."""
    req = (request_text or "").lower()
    low = (product_text or "").lower()
    for a, b in _SUBTYPE_PAIRS:
        for want, other in ((a, b), (b, a)):
            if (
                _has_stem(req, want)
                and not _has_stem(req, other)
                and _has_stem(low, other)
                and not _has_stem(low, want)
            ):
                return True
    return False


def _subtype_ambiguous_from_title(product: CatalogProduct, request_text: str) -> bool:
    """В запросе есть подтип, но в названии товара его не различить — нужна карточка."""
    if not _request_has_subtype_intent(request_text):
        return False
    low = product.title.lower()
    for a, b in _SUBTYPE_PAIRS:
        if _has_stem(low, a) or _has_stem(low, b):
            return False
    return True


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
    # Только ширина С ЕДИНИЦАМИ (60 см / 600 мм). Голые трёхзначные числа НЕ
    # берём — это номера моделей (Millen MEO 603, Lex EDP 607), а не ширина.
    low = title.lower()
    m = re.search(r"(\d{2,3})\s*см\b", low)
    if m:
        return int(m.group(1)) * 10
    m = re.search(r"(\d{3,4})\s*мм\b", low)
    if m:
        return int(m.group(1))
    return None


def _install_family(text: str) -> str | None:
    """Тип установки: встраиваемый / отдельностоящий (по тексту)."""
    t = text.lower()
    if "отдельностоящ" in t or "отдельно стоящ" in t:
        return "отдельностоящий"
    if "встраив" in t or "встро" in t:  # встраиваемая, полновстраиваемая, встроенная
        return "встраиваемый"
    return None


def _product_install(product: CatalogProduct) -> str | None:
    val = _attr_get(product, "установк", "монтаж")
    return (_install_family(val) if val else None) or _install_family(product.title)


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


# ── Материал (мойки, поверхности) — жёсткий фильтр как цвет ───────────────────
_MATERIAL_TOKENS: dict[str, set[str]] = {
    "нержавейка": {"нержав", "инокс"},
    "камень": {"камен", "гранит", "кварц", "мрамор", "агломерат"},
    "керамика": {"керамик", "фарфор"},
    "стекло": {"стекл"},
    "эмаль": {"эмал"},
    "композит": {"композит"},
    "пластик": {"пластик"},
}


def _canon_material(s: str | None) -> str | None:
    low = (s or "").lower()
    for canon, toks in _MATERIAL_TOKENS.items():
        if any(t in low for t in toks):
            return canon
    return low.strip() or None


def _product_material(product: CatalogProduct) -> str | None:
    val = _attr_get(product, "материал")
    return _canon_material(val) if val else None


# ── Класс энергопотребления (порядковый: A+++ лучше G) ────────────────────────
_ENERGY_ORDER = ("A+++", "A++", "A+", "A", "B", "C", "D", "E", "F", "G")


def _canon_energy(s: str | None) -> str | None:
    if not s:
        return None
    t = str(s).upper().split("/")[0].strip()
    t = t.translate(str.maketrans("АВСЕ", "ABCE"))  # кириллица → латиница
    m = re.match(r"[A-G]\+*", t)
    return m.group() if m else None


def _energy_rank(s: str | None) -> int | None:
    c = _canon_energy(s)
    return _ENERGY_ORDER.index(c) if c in _ENERGY_ORDER else None


def _num(s: object) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", str(s))
    return float(m.group().replace(",", ".")) if m else None


def _product_volume(product: CatalogProduct) -> int | None:
    val = _attr_get(product, "объ")  # «Общий/полезный объем, л», «Объем, л»
    n = _num(val) if val else None
    return int(n) if n is not None else None


def _product_load(product: CatalogProduct) -> float | None:
    val = _attr_get(product, "загруз")  # «Max загрузка белья, кг»
    return _num(val) if val else None


def _product_energy(product: CatalogProduct) -> str | None:
    return _attr_get(product, "класс энерг")  # «Класс энергопотребления»


# ── Универсальное сопоставление ЛЮБОЙ характеристики (spec.extra) ─────────────
def _find_attr_value(product: CatalogProduct, name: str) -> str | None:
    """Значение атрибута товара, ключ которого лучше всего соответствует `name`.

    Клиент называет характеристику своими словами («отжим», «камер», «программ»),
    а у товара ключ полнее («Скорость отжима, оборотов/мин»). Сначала прямое
    вхождение подстроки, затем — совпадение по токенам.
    """
    attrs = product.attributes or {}
    nlow = name.lower().strip()
    if not nlow:
        return None
    for k, v in attrs.items():
        if nlow in k.lower():
            return v
    ntoks = {t for t in re.split(r"[^а-яёa-z0-9]+", nlow) if len(t) >= 3}
    if not ntoks:
        return None
    best, best_score = None, 0
    for k, v in attrs.items():
        ktoks = set(re.split(r"[^а-яёa-z0-9]+", k.lower()))
        score = len(ntoks & ktoks)
        if score > best_score:
            best, best_score = v, score
    return best if best_score else None


def _value_matches(requested: str, product_val: str) -> bool:
    """Совпадает ли желаемое значение характеристики с фактическим у товара.

    Числа — с допуском ±15%; иначе — вхождение подстроки (регистр не важен).
    """
    rn, pn = _num(requested), _num(product_val)
    if rn is not None and pn is not None:
        return abs(pn - rn) <= max(rn * 0.15, 0.5)
    r, p = str(requested).lower().strip(), str(product_val).lower()
    return bool(r) and (r in p or p in r)


def _constraint_result(product: CatalogProduct, c) -> bool | None:
    """Выполняется ли ограничение по характеристике (attr_constraint) у товара.

    None — атрибута у товара нет (неизвестно, «молчание»): в фильтре не отсекаем.
    Операторы: gte (≥), lte (≤), approx (≈±15%), eq (точное/категориальное; для
    класса энергопотребления — «равен или лучше»).
    """
    attrs = product.attributes or {}
    pv = attrs.get(c.attribute)
    if pv is None:
        pv = _find_attr_value(product, c.attribute)  # запасной нечёткий поиск ключа
    if pv is None or not str(pv).strip():
        return None
    op = getattr(c, "op", "eq")
    if op in ("gte", "lte", "approx"):
        rn, pn = _num(c.value), _num(pv)
        if rn is None or pn is None:
            return _value_matches(c.value, pv)
        if op == "gte":
            return pn >= rn * 0.98
        if op == "lte":
            return pn <= rn * 1.02
        return abs(pn - rn) <= max(rn * 0.15, 0.5)
    # eq: класс энергопотребления — «равен или лучше», иначе категориальное вхождение
    if "класс энерг" in c.attribute.lower():
        wr, pr = _energy_rank(c.value), _energy_rank(pv)
        if wr is not None and pr is not None:
            return pr <= wr
    return _value_matches(c.value, pv)


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
        """Кандидаты, прошедшие фильтры, отсортированные по взвешенному скорингу.

        Если под все требования не нашлось ничего, требования снимаются ПО ОЧЕРЕДИ,
        и порядок здесь принципиален: первым гнётся ГАБАРИТ, и только потом — тип
        техники, названный клиентом.

        Раньше было наоборот, и на живом запросе «вытяжка полновстр. 450» бот
        выбрасывал условие «полновстраиваемая», держался за 450 мм и выдавал
        ОСТРОВНУЮ вытяжку за 92 990 ₽ — при том что полновстраиваемые того же
        бренда шириной 495 мм стоят 13 690 ₽. Островная и встраиваемая — разные
        по сути приборы (одна висит под потолком, вторая прячется в шкаф), а
        ±50 мм ширины клиент переживёт. Тип важнее габарита.
        """
        # Порядок смягчений: чем дальше, тем больше требований снято.
        relaxations: list[dict] = [
            {},                                                 # всё, как просил клиент
            {"ignore_dims": True},                              # гнём габарит/конфорки
            {"ignore_constraints": True},                       # гнём характеристики
            {"ignore_constraints": True, "ignore_dims": True},  # последняя попытка
        ]
        priced: list[CatalogProduct] = []
        for kwargs in relaxations:
            filtered = [c for c in candidates if self._passes_filters(spec, c, **kwargs)]
            priced = [c for c in filtered if c.price is not None]
            if priced:
                break
        if not priced:
            return []
        # Ценовой потолок («до 30000»): оставляем только то, что в бюджет. Если в
        # бюджет не влез никто — не обнуляем выдачу, ранжируем всех (тогда подбор
        # вернёт ближайшее к бюджету, а не «не найдено»).
        if spec.max_price:
            within = [c for c in priced if (c.price or 0.0) <= spec.max_price]
            if within:
                priced = within
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

    def needs_enrichment(self, spec: ProductSpec, product: CatalogProduct) -> bool:
        """Нужно ли открыть карточку товара перед финальным решением.

        Обогащаем, если запрошенная характеристика не подтверждена названием/кэшем
        атрибутов — иначе подбор по «молчанию» пропускает неверный товар.
        """
        if spec.color and not self.color_confirmed_by_title(spec, product):
            return True
        if spec.width_mm and _product_width(product) is None:
            return True
        if spec.fuel_type and not _product_fuels(product):
            return True
        if spec.burners and _product_burners(product) is None:
            return True
        if spec.install_type and _product_install(product) is None:
            return True
        if spec.raw_text and _subtype_ambiguous_from_title(product, spec.raw_text):
            return True
        return False

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

    def _passes_filters(
        self, spec: ProductSpec, product: CatalogProduct, ignore_constraints: bool = False,
        ignore_dims: bool = False,
    ) -> bool:
        # Конфликт подтипов («кофемашина» vs кофеварка, «камера» vs ларь): клиент
        # явно назвал подтип — противоположный в названии/атрибутах отсеиваем.
        if spec.raw_text and _subtype_conflict(spec.raw_text, _product_subtype_text(product)):
            return False

        # Ограничения по характеристикам (схемо-маппер): eq/gte/lte — жёстко
        # отсекаем при ЯВНОМ несоответствии; approx — только сигнал (не фильтр);
        # неизвестное значение («молчание») — не отсекаем.
        if not ignore_constraints:
            for c in spec.attr_constraints:
                if c.op == "approx":
                    continue
                if _constraint_result(product, c) is False:
                    return False

        colors = _product_colors(product)

        # Жёсткие исключения по цвету («не чёрную») и топливу («кроме газовых»).
        for excluded in spec.exclude_colors:
            if excluded and _canon_color(excluded) in colors:
                return False
        if spec.exclude_fuel:
            fuels = _product_fuels(product)
            for excluded in spec.exclude_fuel:
                if excluded and _canon_fuel(excluded) in fuels:
                    return False

        # Позитивная валидация (#1): отбрасываем только при ЯВНОМ противоречии
        # (заявлен другой цвет/топливо/число конфорок). Молчание — ок. Сигналы
        # берутся из названия И характеристик товара (#4).
        if spec.color and _color_conflict(_canon_color(spec.color), colors):
            return False
        if spec.fuel_type and _fuel_conflict(_canon_fuel(spec.fuel_type), _product_fuels(product)):
            return False
        if spec.burners and not ignore_dims:
            b = _product_burners(product)
            if b is not None and b != spec.burners:
                return False
        # Духовой шкаф с СВЧ (комби 2-в-1) не подставляем под простой «духовой шкаф»,
        # если клиент не просил СВЧ — дороже и дублирует отдельную микроволновку.
        # Снимается смягчением (ignore_dims): если у бренда только комби — вернём его.
        if (spec.appliance is ApplianceType.OVEN and not ignore_dims
                and "свч" not in (spec.raw_text or "").lower()
                and "микроволн" not in (spec.raw_text or "").lower()
                and _is_combi_microwave_oven(product)):
            return False
        # Тип установки: встраиваемый под запрос «встраиваемый» и наоборот
        # (#2 — иначе под «встраиваемый холодильник» мог попасть отдельностоящий).
        if spec.install_type:
            want = _install_family(spec.install_type)
            have = _product_install(product)
            if want and have and want != have:
                return False
        # Жёсткая ширина: если в карточке ширина известна и не попадает в допуск —
        # отсекаем. Молчание (ширина не указана) — не конфликт. ignore_dims снимает
        # (напр. «духовка 450» = высота компакта, а не ширина — не должна обнулять).
        if spec.width_mm and not ignore_dims:
            w = _product_width(product)
            if w is not None and abs(w - spec.width_mm) > _WIDTH_TOLERANCE_MM:
                return False
        # Материал (мойка из камня / нержавейки): явное противоречие — отсекаем.
        if spec.material:
            want_mat = _canon_material(spec.material)
            have_mat = _product_material(product)
            if want_mat and have_mat and want_mat != have_mat:
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
            if w is not None and abs(w - spec.width_mm) <= _WIDTH_TOLERANCE_MM:
                matched += 1
        if spec.volume_l:
            requested += 1
            v = _product_volume(product)
            if v is not None and abs(v - spec.volume_l) <= max(spec.volume_l * 0.15, 10):
                matched += 1
        if spec.load_kg:
            requested += 1
            ld = _product_load(product)
            if ld is not None and abs(ld - spec.load_kg) <= 1.0:
                matched += 1
        if spec.energy_class:
            want_rank = _energy_rank(spec.energy_class)
            if want_rank is not None:
                requested += 1
                pr = _energy_rank(_product_energy(product))
                if pr is not None and pr <= want_rank:  # класс равен или лучше
                    matched += 1
        if spec.material:
            requested += 1
            if _product_material(product) == _canon_material(spec.material):
                matched += 1
        # Универсально — любые прочие характеристики из запроса (spec.extra).
        for name, value in (spec.extra or {}).items():
            if not value:
                continue
            requested += 1
            pv = _find_attr_value(product, name)
            if pv is not None and _value_matches(value, pv):
                matched += 1
        # Ограничения схемо-маппера — точный сигнал по реальным атрибутам.
        for c in spec.attr_constraints:
            requested += 1
            if _constraint_result(product, c) is True:
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
