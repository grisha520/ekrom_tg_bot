"""Краулер раздела «Акции» ekrom и датасет комплектных кампаний (Ф1 проекта комплектов).

Раздел `/catalog/aktsii/` — это НАБОР кампаний, каждая на своей странице
`/catalog/aktsii/<slug>/`. Верхняя страница карточек товаров не содержит — только
плитки-ссылки на кампании. Внутри кампании — обычная разметка каталога
(`.product-card` с `data-product-id`, цена, иногда старая цена), но с жёстким
потолком в 21 «витринную» позицию (без пагинации и «показать ещё»): для кампаний
«весь ассортимент бренда» это лишь витрина, полный состав участников выводится
применением правил к товарам бренда из каталога (кэш) на этапе Ф4.

Механика скидки на странице кампании НЕ проставлена per-товар (у комплектных акций
старой/промо-цены у карточек нет) — она описана ТЕКСТОМ правил под заголовком.
Проверено вживую: корзина ekrom комплектную скидку НЕ применяет автоматически
(ИТОГО = сумма цен), её ставит менеджер «Индивидуальной скидкой». Поэтому итог
комплекта бот считает сам из закодированных правил кампании (Ф2), а не берёт с
сайта. Здесь мы собираем сырьё: бренд, тип механики, срок, текст правил, тарифную
сетку (кол-во позиций → %) и витринных участников.
"""

from __future__ import annotations

import html as _html
import logging
import re
from dataclasses import dataclass, field
from datetime import date

import httpx

from ..config import Settings, get_settings
from .crawler import _ENRICH_HEADERS, _ekrom_cookies

logger = logging.getLogger(__name__)

PROMO_ROOT = "/catalog/aktsii/"

# Пять брендов проекта комплектов (нижний регистр для сравнения).
TARGET_BRANDS: tuple[str, ...] = ("korting", "maunfeld", "midea", "lex", "kuppersberg")
# Как бренд пишется в заголовках/названиях (для аккуратного вывода).
_BRAND_DISPLAY = {
    "korting": "Korting", "maunfeld": "Maunfeld", "midea": "Midea",
    "lex": "Lex", "kuppersberg": "Kuppersberg",
}


class PromoMechanic(str):
    """Тип механики кампании (какого рода выгода и как считается итог).

    Строковые константы (не Enum) — чтобы прозрачно писать/читать из SQLite.
    """

    CASCADE_CHEAPEST = "cascade_cheapest"  # % на наименьший по стоимости, растёт с числом позиций
    FLAT_COMBO = "flat_combo"              # плоский % на весь комплект, растёт с числом позиций
    GIFT = "gift"                          # техника/аксессуар в подарок
    SPEC_PRICE = "spec_price"              # спец-цена/скидка на конкретную позицию
    SHOWCASE = "showcase"                  # витрина/товары месяца (разовые уценённые)
    UNKNOWN = "unknown"                    # правила не распознаны — итог уточняет менеджер


@dataclass
class PromoItem:
    """Витринный участник кампании (то, что показано на странице акции)."""

    product_id: str | None
    url: str
    title: str
    brand: str | None
    price: float | None
    old_price: float | None = None


@dataclass
class SpecPriceRule:
    """«Доп. предмет по спеццене при комплекте» (главная механика оплаченных подборок).

    Kuppersberg: «Из 3-х предметов → 4-й предмет по цене … RBN 1761 за 40 000 руб» →
    tiers={3: 40000, 4: 27000}, models=["RBN 1761", "RBN 1965"]. Korting: «скидка 50%
    на встраиваемый холодильник KSI 19547» → pct=50, models=["KSI 19547"]. line_label —
    особое условие «духовой из линейки „Эксклюзив“»: проверить по каталогу нельзя
    (шильдик на сайте), такие считаем с пометкой менеджеру.
    """

    models: list[str]
    tiers: dict[int, float] = field(default_factory=dict)  # кол-во предметов → фикс-цена, руб
    pct: float | None = None                                # либо −% на модель
    line_label: str | None = None                           # «Эксклюзив» и т.п.


@dataclass
class GiftRule:
    """Оцифрованное условие подарочной акции (для сравнения со скидками).

    condition — по какому порогу даётся подарок: «sum» (сумма покупки, руб) или
    «count» (число позиций из разных категорий). options — порог → ключ модели
    подарка для поиска в каталоге (напр. {10000: "KWK 0902"}). Оцифровываем только
    чёткие механики; «на выбор»/«спец-цена»/баннерные без текста → None (менеджер).
    """

    condition: str                 # "sum" | "count"
    options: dict[int, str]         # порог → ключ модели подарка


@dataclass
class PromoCampaign:
    """Кампания раздела «Акции» с разобранными правилами и витринными участниками."""

    slug: str
    url: str
    title: str
    brand: str | None
    mechanic: str
    period_from: str | None = None       # ISO-дата начала, если распознали
    period_to: str | None = None         # ISO-дата конца
    scope: str = "unspecified"           # all_brand | listed | unspecified
    rules_text: str = ""
    tiers: dict[int, float] = field(default_factory=dict)  # кол-во позиций → % скидки
    requires_oven: bool = False          # правило «в комплекте обязателен духовой шкаф»
    gift: GiftRule | None = None         # оцифрованное условие подарка (для механики gift)
    spec: SpecPriceRule | None = None    # «доп. предмет по спеццене» (для spec_price)
    exclusions: list[str] = field(default_factory=list)  # не участвуют: домино/отдельностоящ/…
    items: list[PromoItem] = field(default_factory=list)


# ── Разбор HTML ──────────────────────────────────────────────────────────────

_RE_TAG = re.compile(r"<[^>]+>")
_RE_WS = re.compile(r"\s+")
_RE_CARD = re.compile(r'<div class="product-card ')  # начало карточки товара


def _text(fragment: str) -> str:
    """Снять теги/сущности и схлопнуть пробелы (для текстовых блоков)."""
    t = _html.unescape(_RE_TAG.sub(" ", fragment or ""))
    return _RE_WS.sub(" ", t).replace("\xa0", " ").strip()


def _price(text: str | None) -> float | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return float(digits) if digits else None


def brand_from_text(text: str | None) -> str | None:
    """Определить один из пяти брендов по названию/заголовку (или None)."""
    low = (text or "").lower()
    for b in TARGET_BRANDS:
        if b in low:
            return _BRAND_DISPLAY[b]
    return None


# Дата с возможными пробелами внутри: «30. 09. 2026», «14 .01.2026» — так пишут в
# правилах на сайте.
_RE_DATE = re.compile(r"(\d{1,2})\s?[.\-]\s?(\d{1,2})\s?[.\-]\s?(\d{2,4})")
# Предлог НЕПОСРЕДСТВЕННО перед датой задаёт её роль.
_RE_FROM_PREP = re.compile(r"(?:^|[\s(])(?:с|от|начиная\s+с)\s*$")
_RE_TO_PREP = re.compile(r"(?:^|[\s(])(?:до|по)\s*$")


def _iso_date(m: re.Match) -> str | None:
    """Совпадение даты → ISO-строка (двузначный год = 20xx) или None, если её нет."""
    d, mo, y = m.groups()
    yr = int(y)
    if yr < 100:
        yr += 2000
    try:
        return date(yr, int(mo), int(d)).isoformat()
    except ValueError:
        return None


def parse_period(rules: str) -> tuple[str | None, str | None]:
    """Достать даты начала/конца из текста правил → ISO (YYYY-MM-DD) или None.

    Роль даты определяет предлог ПЕРЕД НЕЙ, а не наличие слова где-то в тексте:
    «с 01.01.2026 - до уведомления» — это дата НАЧАЛА с открытым концом, но слово
    «до» здесь относится к «уведомлению». Раньше такую дату записывали в конец, и
    живые акции Midea выглядели истёкшими. Без предлогов работает позиционное
    правило диапазона: «01.06.26 – 31.07.26» → начало, конец.
    """
    text = _RE_WS.sub(" ", rules or "")
    marked: list[tuple[str, str | None]] = []
    for m in _RE_DATE.finditer(text):
        iso = _iso_date(m)
        if iso is None:
            continue
        before = text[max(0, m.start() - 14):m.start()].lower()
        if _RE_FROM_PREP.search(before):
            role = "from"
        elif _RE_TO_PREP.search(before):
            role = "to"
        else:
            role = None
        marked.append((iso, role))
    if not marked:
        return None, None

    pfrom = next((d for d, r in marked if r == "from"), None)
    pto = next((d for d, r in marked if r == "to"), None)
    free = [d for d, r in marked if r is None]
    if pfrom is None and pto is None:
        # Диапазон без предлогов: первая дата — начало, вторая — конец.
        return (free[0], free[1]) if len(free) >= 2 else (free[0], None)
    if pfrom is None and free:
        pfrom = next((d for d in free if d <= pto), None) if pto else free[0]
    elif pto is None and free:
        pto = next((d for d in free if d >= pfrom), None)
    return pfrom, pto


_WORD_N = {"двух": 2, "трех": 3, "трёх": 3, "четырех": 4, "четырёх": 4,
           "пяти": 5, "шести": 6, "семи": 7}
_COUNT = r"(двух|тр[её]х|четыр[её]х|пяти|шести|семи|\d)"
_UNIT = r"(?:прибор|предмет|единиц|товар)"


def _to_n(token: str) -> int | None:
    if token in _WORD_N:
        return _WORD_N[token]
    return int(token) if token.isdigit() else None


def parse_tiers(rules: str) -> dict[int, float]:
    """Сетка «кол-во позиций → % скидки» из текста правил (для Ф2).

    Формулировки на сайте двух видов, и порядок «число ↔ процент» в них РАЗНЫЙ:
      • процент-впереди: «Скидка <P>% на комплект из <N> единиц» (Kuppersberg-комплект);
      • число-впереди: «при покупке <N> приборов … <P>%», «<N>-х предметов … <P>%»,
        «комплекта из <N>-х приборов … скидку <P>%» (каскад, Maunfeld).
    Пары нельзя мэтчить «число→ближайший %» вслепую: у процент-впереди это даёт
    сдвиг на тариф. Поэтому сначала пробуем явный процент-впереди («…% …комплект из
    N…»); если он дал сетку — берём её, иначе — число-впереди. Пусто — сетку не
    распознали (механика подарок/спец-цена/без текста → итог уточнит менеджер).
    """
    low = _RE_WS.sub(" ", (rules or "").lower())

    # процент-впереди: «<P>% … комплект из <N>»
    percent_first: dict[int, float] = {}
    for m in re.finditer(r"(\d{1,3})\s*%[^%]{0,40}?комплект\w*\s+из\s+(\d)", low):
        n = int(m.group(2))
        if 2 <= n <= 7:
            percent_first[n] = float(m.group(1))
    if len(percent_first) >= 2:
        return percent_first

    # число-впереди: «<N> приборов/предметов/единиц … <P>%» (первый % после числа).
    # Связка числа и единицы бывает «2-х», «5-и», «6-ти» — допускаем дефис+окончание.
    count_first: dict[int, float] = {}
    for m in re.finditer(_COUNT + r"(?:-[а-яё]{1,3})?\s*" + _UNIT + r"[а-яё]*[^%]{0,120}?(\d{1,3})\s*%", low):
        n = _to_n(m.group(1))
        if n and 2 <= n <= 7:
            count_first.setdefault(n, float(m.group(2)))
    return count_first or percent_first


# Ключ модели товара ekrom: латинский код («KWK 0902», «RBN 1761», «MID45S100I»).
# Слитные буквенно-цифровые хвосты обязательно захватываем целиком — иначе
# «MID45S100I» обрезался до «MID45» и подарочная скидка матчила ЛЮБУЮ ПММ MID45*.
_MODEL = r"[A-Z]{2,}\d*[A-Z0-9]*(?:\s\d+)?"


def parse_gift(rules: str) -> "GiftRule | None":
    """Оцифровать условие подарка из текста правил (или None — оставить менеджеру).

    Распознаём три чётких шаблона:
      • по сумме: «при покупке … на сумму <N> руб … в подарок <МОДЕЛЬ>»;
      • по числу позиций: «<МОДЕЛЬ> … в подарок … при покупке <N> приборов»;
      • лестница подарков: «при покупке <N> приборов – в подарок … <МОДЕЛЬ>;
        при покупке <N+1> приборов – …» (Korting «Формула выгоды»: 2 → вытяжка,
        3 → духовой шкаф, 4 → ПММ; из «<A> или <B>» берём первую модель).
    «На выбор»/«или спец-цена»/баннерные без текста НЕ оцифровываем (слишком
    вариативно → правило заказчика «не считать неверно»).
    """
    if not rules:
        return None
    low = rules.lower()
    if "на выбор" in low or "спец цен" in low or "спец. цен" in low:
        return None

    # По сумме покупки (может быть несколько порогов).
    sum_opts: dict[int, str] = {}
    for m in re.finditer(r"на сумму\s*([\d ]+?)\s*руб.*?(" + _MODEL + r")", rules, re.S):
        thr = int(re.sub(r"\D", "", m.group(1)) or 0)
        model = _RE_WS.sub(" ", m.group(2)).strip()
        if thr and model:
            sum_opts[thr] = model
    if sum_opts:
        return GiftRule("sum", sum_opts)

    # Лестница «при покупке N приборов – в подарок … МОДЕЛЬ» (модель ПОСЛЕ
    # «в подарок», порогов может быть несколько; сегменты разделены ;/.).
    ladder: dict[int, str] = {}
    for m in re.finditer(
        r"при покупке\s*(\d)(?:-[а-яё]{1,3})?\s*" + _UNIT
        + r"[а-яё]*[^.;]{0,60}?в подарок[^.;]{0,150}?(" + _MODEL + r")",
        rules, re.S,
    ):
        n = int(m.group(1))
        if 2 <= n <= 7:
            ladder.setdefault(n, _RE_WS.sub(" ", m.group(2)).strip())
    if ladder:
        return GiftRule("count", ladder)

    # По числу позиций: модель перед «в подарок», порог — «при покупке N».
    m = re.search(r"(" + _MODEL + r")[^.]{0,80}?в подарок[^.]{0,80}?при покупке\s*(\d)", rules, re.S)
    if m:
        return GiftRule("count", {int(m.group(2)): _RE_WS.sub(" ", m.group(1)).strip()})
    m = re.search(r"при покупке\s*(\d)[^.]{0,120}?(" + _MODEL + r")[^.]{0,40}?в подарок", rules, re.S)
    if m:
        return GiftRule("count", {int(m.group(1)): _RE_WS.sub(" ", m.group(2)).strip()})
    return None


def parse_spec_price(rules: str) -> "SpecPriceRule | None":
    """Оцифровать механику «доп. предмет по спеццене» из текста условий (или None).

    Два шаблона с сайта:
      • «Из <N>-х предметов → … <МОДЕЛЬ> за <цена> руб» (Kuppersberg, пороги 3/4);
      • «скидка <P>% на встраиваемый холодильник <МОДЕЛЬ>» (Korting; в тексте
        встречается разрыв «с кидка» из-за тега внутри слова — терпим к нему).
    """
    txt = _RE_WS.sub(" ", rules or "")
    tiers: dict[int, float] = {}
    models: list[str] = []
    parts = re.split(r"(?i)из\s+(\d)\s*[-–]?\s*х?\s*предмет", txt)
    for i in range(1, len(parts) - 1, 2):
        n = int(parts[i])
        for m in re.finditer(r"(" + _MODEL + r")\s*за\s*([\d\s]+?)\s*руб", parts[i + 1]):
            price = float(re.sub(r"\D", "", m.group(2)) or 0)
            model = _RE_WS.sub(" ", m.group(1)).strip()
            if price and model:
                tiers[n] = price
                if model not in models:
                    models.append(model)
    line = re.search(r'линейк\w*\s*[«"]?\s*(эксклюзив\w*)', txt, re.I)
    if tiers and models:
        return SpecPriceRule(models=models, tiers=tiers,
                             line_label=line.group(1).capitalize() if line else None)
    m = re.search(
        r"с\s?кидк\w*\s*(\d{1,3})\s*%\s*на\s+встраиваемый\s+холодильник\s+(" + _MODEL + r")",
        txt, re.I,
    )
    if m:
        return SpecPriceRule(models=[_RE_WS.sub(" ", m.group(2)).strip()],
                             pct=float(m.group(1)),
                             line_label=line.group(1).capitalize() if line else None)
    return None


# Формулировки «не участвуют» → канонический ключ исключения (для движка расчёта).
# «двухконфорочные варочные» = домино («Формула выгоды» Korting); «отдельностоящая
# микроволновая печь» — это соло-СВЧ, а НЕ бланкетное исключение отдельностоящей
# техники (негативный lookahead), иначе кампания «участвует отдельностоящая и
# встраиваемая» теряла бы отдельностоящие приборы.
_EXCLUSION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"домино|двухконфороч", "домино"),
    (r"отдельностоящ\w*\b(?!\s+(?:микроволнов|свч))", "отдельностоящая"),
    (r"соло\s*свч|отдельностоящ\w*\s+(?:микроволнов|свч)", "соло-свч"),
    (r"измельчител", "измельчитель"),
    (r"соло\s*кофемашин", "соло-кофемашина"),
    (r"(?:мелк|мал)\w*\s+бытов\w*|\bмбт\b", "мбт"),
)

# Маркер запрещающей формулировки и граница предложения (точка/точка с запятой
# перед пробелом) — исключения ищем в границах ОДНОГО предложения.
_RE_EXCL_MARKER = re.compile(r"не\s+участв|кроме|исключ")
_RE_SENTENCE = re.compile(r"[.;]\s")


def parse_exclusions(rules: str) -> list[str]:
    """Категории, НЕ участвующие в акции (влияют на счёт позиций и базу скидки).

    Ловим только явные формулировки «не участвуют / кроме …», причём в границах
    ОДНОГО предложения: соседняя фраза со своим смыслом не должна попадать под
    запрет. Реальный кейс «Формулы выгоды» Korting — «участвует весь ассортимент
    (отдельностоящая и встраиваемая). В акции не участвуют: отдельностоящая
    микроволновая печь…»: по окну ±90 символов первая фраза ложно читалась как
    исключение всей отдельностоящей техники.
    """
    low = _RE_WS.sub(" ", (rules or "").lower())
    banned = [s for s in _RE_SENTENCE.split(low) if _RE_EXCL_MARKER.search(s)]
    out: list[str] = []
    for pat, key in _EXCLUSION_PATTERNS:
        if any(re.search(pat, seg) for seg in banned):
            out.append(key)
    return out


def classify_mechanic(title: str, rules: str) -> str:
    """Классифицировать механику кампании по заголовку и тексту правил."""
    blob = f"{title} {rules}".lower()
    if "наименьш" in blob or "каскад" in blob:
        return PromoMechanic.CASCADE_CHEAPEST
    if "витрин" in blob or "товары месяца" in blob:
        return PromoMechanic.SHOWCASE
    if "комплект" in blob and re.search(r"\d{1,3}\s*%", blob):
        return PromoMechanic.FLAT_COMBO
    if "подар" in blob:
        return PromoMechanic.GIFT
    if "спец" in blob or re.search(r"скидк\w*\s+\d{1,3}\s*%", blob):
        return PromoMechanic.SPEC_PRICE
    if re.search(r"\d{1,3}\s*%", blob):
        return PromoMechanic.FLAT_COMBO
    return PromoMechanic.UNKNOWN


def parse_campaign_items(html_text: str, base_url: str) -> list[PromoItem]:
    """Разобрать витринные карточки товаров со страницы кампании.

    Идём по чанкам `.product-card`: из каждого берём data-product-id, ссылку на
    товар, название, текущую и (если есть) старую цену. Бренд — из названия.
    """
    base = base_url.rstrip("/")
    items: list[PromoItem] = []
    parts = _RE_CARD.split(html_text)
    for chunk in parts[1:]:
        pid = re.search(r'data-product-id="(\d+)"', chunk)
        href = re.search(r'href="(/products/[^"]+)"', chunk)
        if not href:
            continue
        title_m = re.search(r'href="/products/[^"]+"\s+title="([^"]*)"', chunk)
        title = _text(title_m.group(1)) if title_m else ""
        title = re.sub(r"^картинка\s+", "", title, flags=re.I)
        price_m = re.search(r'catprice_\d+\s*">\s*([^<]*?)\s*</span>', chunk)
        old_m = re.search(r'catoldprice_\d+\s*">\s*([^<]*?)\s*</span>', chunk)
        items.append(
            PromoItem(
                product_id=pid.group(1) if pid else None,
                url=f"{base}{href.group(1)}",
                title=title,
                brand=brand_from_text(title),
                price=_price(price_m.group(1)) if price_m else None,
                old_price=_price(old_m.group(1)) if old_m else None,
            )
        )
    return items


def _extract_title(html_text: str) -> str:
    """Заголовок кампании: на страницах акций он в `.title--banner <h2>`, не в <h1>."""
    m = re.search(r'title--banner[^>]*>\s*<h2[^>]*>(.*?)</h2>', html_text, re.S)
    if m:
        return _text(m.group(1))
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.S)
    return _text(m.group(1)) if m else ""


def _extract_rules(html_text: str) -> str:
    """Текст условий кампании — блок `<div class="action-descr__inner">` под баннером.

    Раньше брали фрагмент «от <h1>», но на страницах акций заголовок лежит в
    `.title--banner`, а условия — в `action-descr`, из-за чего часть кампаний
    (в т.ч. «холодильник по спеццене») выглядела пустой. Фолбэк на старый способ
    оставлен для нетипичной разметки; хвост режем по известным маркерам «шума».
    """
    k = html_text.find("action-descr__inner")
    if k >= 0:
        start = html_text.find(">", k) + 1
        seg_raw = html_text[start : start + 12000]
        # Начало следующего блока (список товаров/фильтр) — конец условий.
        cut = seg_raw.find("<div class=")
        if cut > 0:
            seg_raw = seg_raw[:cut]
        seg = _text(seg_raw)
    else:
        k = html_text.find("<h1")
        if k < 0:
            return ""
        seg = _text(html_text[k : k + 4000])
    for marker in ("Комплекты для постирочной", "Товары, участвующие в акции",
                   "Аксессуары для кухонной"):
        seg = seg.split(marker)[0]
    return seg.strip()


def parse_campaign(
    html_text: str, slug: str, base_url: str,
    title_hint: str | None = None, period_hint: str | None = None,
) -> PromoCampaign:
    """Собрать PromoCampaign из HTML страницы кампании.

    title_hint/period_hint — с плитки раздела «Акции» (верхняя страница). Часть
    кампаний описана баннером-картинкой без текста: у их страниц нет ни H1, ни
    правил, зато на плитке есть заголовок и срок — используем их как запасной
    источник, иначе такие кампании остаются безымянными и не классифицируются.
    """
    title = _extract_title(html_text) or (title_hint or "")
    rules = _extract_rules(html_text)
    brand = brand_from_text(f"{slug} {title}")
    # Спеццена распознаётся ДО классификатора: в её тексте есть и «подарок», и
    # «скидка N%», из-за чего классификатор уводит кампанию в gift/flat_combo.
    spec = parse_spec_price(rules)
    mechanic = PromoMechanic.SPEC_PRICE if spec else classify_mechanic(title, rules)
    # Срок берём ПРЕЖДЕ ВСЕГО с плитки раздела: там сайт показывает собственные
    # поля активности кампании, а проза правил бывает устаревшей — у «подарка за
    # новинку» Maunfeld в тексте стоит 05.08.2026, хотя сайт продлил акцию до
    # 31.08.2026 и продолжает её показывать. Текст правил — запасной источник.
    pfrom, pto = parse_period(period_hint or "")
    if not (pfrom or pto):
        pfrom, pto = parse_period(rules)
    low_rules = rules.lower()
    # Охват акции: «весь ассортимент» → all_brand; ЯВНОЕ «участвуют только …» →
    # listed (скидка лишь на перечисленные модели — движок сверит позиции со
    # списком участников). Без явной формулировки — unspecified: витрина на
    # странице кампании ограничена 21 карточкой и НЕ равна составу акции,
    # поэтому по умолчанию список не навязываем (эталоны менеджера это
    # подтверждают: скидка применялась к товарам вне витрины).
    if re.search(r"вес[ья]\s+ассортимент", low_rules):
        scope = "all_brand"
    elif re.search(r"участву\w+\s+только|только\s+(?:на\s+)?(?:модел|выделенн)", low_rules):
        scope = "listed"
    else:
        scope = "unspecified"
    items = parse_campaign_items(html_text, base_url)
    return PromoCampaign(
        slug=slug,
        url=f"{base_url.rstrip('/')}{PROMO_ROOT}{slug}/",
        title=title or slug,
        brand=brand,
        mechanic=mechanic,
        period_from=pfrom,
        period_to=pto,
        scope=scope,
        rules_text=rules,
        tiers=parse_tiers(rules),
        requires_oven=bool(re.search(r"обязательн\w*\s+наличие\s+духов", low_rules)
                           or re.search(r"духов\w+\s+шкаф\w*\s+должен", low_rules)
                           or re.search(r"должен\s+присутствовать\s+духов", low_rules)),
        gift=parse_gift(rules) if mechanic == PromoMechanic.GIFT else None,
        spec=spec,
        exclusions=parse_exclusions(rules),
        items=items,
    )


def discover_campaign_slugs(html_text: str) -> list[str]:
    """Все slug'и кампаний со страницы `/catalog/aktsii/` (по ссылкам)."""
    slugs = sorted(set(re.findall(r"/catalog/aktsii/([a-z0-9_]+)/", html_text)))
    return slugs


def parse_top_index(html_text: str) -> dict[str, dict[str, str]]:
    """Плитки раздела «Акции»: slug → {title, period}.

    Заголовок кампании лежит в `alt` картинки плитки, срок — в `<span>` рядом
    («до 31.12.2026» / «от 11.06.2026»). Это единственный источник названия/срока
    для кампаний, чья страница — только баннер без текста.
    """
    index: dict[str, dict[str, str]] = {}
    for slug, body in re.findall(
        r'href="/catalog/aktsii/([a-z0-9_]+)/"\s+class="actions__item"(.*?)</a>',
        html_text, re.S,
    ):
        alt = re.search(r'alt="([^"]*)"', body)
        spans = [_text(s) for s in re.findall(r"<span>(.*?)</span>", body, re.S)]
        period = next((s for s in spans if re.search(r"\d{2}\.\d{2}\.\d{4}", s)), "")
        index.setdefault(slug, {
            "title": _text(alt.group(1)) if alt else "",
            "period": period,
        })
    return index


# ── Сетевой обход ─────────────────────────────────────────────────────────────

async def crawl_promos(
    settings: Settings | None = None, target_brands_only: bool = True
) -> list[PromoCampaign]:
    """Обойти раздел «Акции»: список кампаний → разбор каждой.

    target_brands_only=True — оставить только кампании пяти брендов проекта
    (бренд определён по slug/заголовку). Мультибрендовые витрины (товары месяца,
    товары с витрины) при этом отсекаются — их участников фильтруем по бренду
    позже, при интеграции с каталогом.
    """
    settings = settings or get_settings()
    base = settings.ekrom_base_url.rstrip("/")
    campaigns: list[PromoCampaign] = []
    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=True, headers=_ENRICH_HEADERS,
        cookies=_ekrom_cookies(settings),
    ) as client:
        top = await client.get(f"{base}{PROMO_ROOT}")
        slugs = discover_campaign_slugs(top.text)
        index = parse_top_index(top.text)
        logger.info("кампаний в разделе «Акции»: %s", len(slugs))
        for slug in slugs:
            try:
                r = await client.get(f"{base}{PROMO_ROOT}{slug}/")
                if r.status_code != 200:
                    logger.warning("кампания %s: HTTP %s", slug, r.status_code)
                    continue
                hint = index.get(slug, {})
                camp = parse_campaign(
                    r.text, slug, base,
                    title_hint=hint.get("title"), period_hint=hint.get("period"),
                )
            except Exception:  # noqa: BLE001 — одна кампания не валит обход
                logger.exception("ошибка разбора кампании %s", slug)
                continue
            if target_brands_only and not camp.brand:
                continue
            campaigns.append(camp)
            logger.info(
                "кампания %s: бренд=%s механика=%s позиций=%s",
                slug, camp.brand, camp.mechanic, len(camp.items),
            )
    return campaigns
