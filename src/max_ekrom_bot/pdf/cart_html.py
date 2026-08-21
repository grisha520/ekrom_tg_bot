"""PDF-комплект «точь-в-точь» как корзина ekrom — через HTML+CSS → Chromium.

Оригинал `cart_pdf.php` сайт рендерит из HTML браузерным движком, поэтому reportlab
не даёт пиксельной идентичности. Здесь мы верстаем ТУ ЖЕ разметку и рендерим уже
встроенным Playwright/Chromium (`page.pdf`), подставляя НАШИ данные и посчитанную
комплектную скидку (которой нет в корзине сайта).

Оформление взято у эталона, а не нарисовано «на глаз»: фирменный шрифт
PPPangramSans и все иконки — оригиналы (шрифт скачан с сайта, иконки извлечены из
эталонного PDF), кегли и координаты колонок выставлены по метрикам, снятым из того
же файла (расхождение ≤ 0.4pt).

Точка входа: `build_cart_html(selection, settings, combo)` → HTML (чистая, тестируемая);
`render_html_pdf(html, out_path)` — async-рендер. Итог собирает `Pipeline.build_kit_pdf`.
"""

from __future__ import annotations

import base64
import html as _html
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from ..models import SelectionResult
from ..showrooms import SHOWROOMS

_ASSETS = Path(__file__).parent / "assets"


# Фирменный шрифт документа корзины. Сайт печатает PDF шрифтом PPPangramSans
# (на вебе он использует Rubik — это разные шаблоны), поэтому наш документ обязан
# использовать именно его, иначе набор отличается на глаз при любой вёрстке.
# Файлы скачаны с ekrom.ru/local/templates/ekrom/fonts/ и лежат в assets/.
_FONT_FACES: tuple[tuple[str, int], ...] = (
    ("PPPangramSans-Light", 300),
    ("PPPangramSans-Medium", 500),
    ("PPPangramSans-Semibold", 600),
)


@lru_cache(maxsize=8)
def _asset_data_uri(filename: str, mime: str = "image/png") -> str:
    """Файл из assets как data:URI (документ остаётся самодостаточным)."""
    try:
        b64 = base64.b64encode((_ASSETS / filename).read_bytes()).decode("ascii")
    except Exception:  # noqa: BLE001 — нет файла: вернём пустоту, вёрстка устоит
        return ""
    return f"data:{mime};base64,{b64}"


@lru_cache(maxsize=1)
def _font_css() -> str:
    """@font-face с зашитыми в base64 файлами шрифта (документ самодостаточен)."""
    faces = []
    for name, weight in _FONT_FACES:
        path = _ASSETS / f"{name}.otf"
        try:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        except Exception:  # noqa: BLE001 — без файла останется системный запасной
            continue
        faces.append(
            f"@font-face{{font-family:'PangramSans';font-style:normal;"
            f"font-weight:{weight};src:url(data:font/otf;base64,{b64}) format('opentype');}}"
        )
    return "".join(faces)


def _logo_svg() -> str:
    try:
        svg = (_ASSETS / "ekrom_logo.svg").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — без логотипа не критично
        return "EKROM"
    return svg.replace("<svg ", '<svg class="logo-svg" ', 1)


# ── Флаги стран ───────────────────────────────────────────────────────────────
# Флаги в документе сайта — векторные, с настоящим гербом (полумесяц у Турции,
# пять звёзд у Китая). Оригиналы извлечены из эталонных PDF и лежат в assets;
# для стран, которых в эталонах не было, остаётся свой круглый флаг из полос.
_FLAG_FILES: dict[str, str] = {
    "австр": "at", "беларус": "by", "болгар": "bg", "великобрит": "gb",
    "венгр": "hu", "герман": "de", "дан": "dk", "евросоюз": "eu",
    "инди": "in", "испан": "es", "итал": "it", "кита": "cn", "литв": "lt",
    "малайз": "my", "польш": "pl", "португал": "pt", "росс": "ru",
    "румын": "ro", "серб": "rs", "словак": "sk", "словен": "si",
    "таиланд": "th", "турц": "tr", "филиппин": "ph", "франц": "fr",
    "чех": "cz", "швейцар": "ch", "корея": "kr", "япон": "jp",
}
# «Австрия» не должна ловиться подстрокой «австр» раньше «Австралии» и наоборот —
# сначала пробуем самые длинные ключи.
_FLAG_ORDER: tuple[str, ...] = tuple(sorted(_FLAG_FILES, key=len, reverse=True))


def _flag(country: str | None) -> str:
    """Флаг страны как в документе сайта.

    Есть файл — рисуем оригинал; нет — серый круг. Своих «похожих» флагов не
    придумываем: для части стран (Швейцария, Индия, Таиланд…) сам сайт печатает
    серый круг, и выдумка отличала бы наш документ от эталона.
    """
    c = (country or "").strip().lower()
    for sub in _FLAG_ORDER:
        if sub in c:
            svg = _icon_svg(f"flag_{_FLAG_FILES[sub]}", 13.5, prefix="")
            if svg:
                return f'<span class="flag">{svg}</span>'
            break
    return '<span class="flag flag--none"></span>'  # серый круг, как в оригинале «-»


# ── Иконки ────────────────────────────────────────────────────────────────────
# ВСЕ иконки — оригиналы из документа сайта, а не нарисованные от руки: «Срок
# гарантии» вынута из эталонного PDF как растр (изображение + маска
# прозрачности), остальные там векторные — их пути извлечены из content stream
# и сохранены в assets как SVG. Свои эскизы заметно отличались от эталона.
def _warranty_icon() -> str:
    uri = _asset_data_uri("icon_warranty.png")
    return f'<img class="ic-warranty" src="{uri}" alt="">' if uri else ""


@lru_cache(maxsize=16)
def _icon_svg(name: str, height_pt: float, prefix: str = "icon_") -> str:
    """SVG из assets, вписанный в заданную высоту (pt). prefix — «icon_» или «»."""
    try:
        svg = (_ASSETS / f"{prefix}{name}.svg").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — без файла блок просто останется без значка
        return ""
    # Свои width/height заменяем на печатные, пропорции держит viewBox.
    svg = re.sub(r'\swidth="[^"]*"', "", svg, count=1)
    svg = re.sub(r'\sheight="[^"]*"', "", svg, count=1)
    return svg.replace("<svg ", f'<svg height="{height_pt}pt" ', 1)


_IC = {
    "truck": lambda: _icon_svg("truck", 13),
    "box": lambda: _icon_svg("box", 13),
    "list": lambda: _icon_svg("list", 13),
    "pin": lambda: _icon_svg("pin", 5.5),
}

# Что и в каком порядке показывать в «Характеристиках» — сверено с документом
# сайта (document (1).pdf, 14.08.2026, комплект Midea из 4 позиций).
#
# Сайт печатает не «первые попавшиеся» атрибуты, а фиксированную шапку
# (артикул → цвет → модель → страна → гарантия → высота → вес/объём), а следом —
# специфичные для категории (конфорки/программы/таймер…). Габариты, кроме
# высоты, в документ не попадают вовсе: ни ширина, ни глубина, ни размеры
# встраивания. Без этого порядка наш PDF показывал сплошные габариты и на
# эталон был не похож.
_ATTR_PRIORITY: tuple[str, ...] = (
    "артикул", "цвет", "модель", "страна производ", "гарантия",
    "высота, мм", "вес", "объем", "объём",
)
# Подстроки полей, которых в документе корзины нет (служебные + габариты).
_HIDE_ATTR_SUBS: tuple[str, ...] = (
    "промо", "остаток поставщика", "производитель, ",
    "ширина", "глубина", "встраивания",
)
_MAX_ATTRS = 11


def _warranty_months(value: str | None) -> str:
    """«24» → «24 месяца» (форма слова по числу, как в документе сайта).

    Дробные значения каталога («36,6») округляем, как это делает сайт: 36,6 → 37.
    Раньше отбрасывалась запятая и получалось «366 месяцев».
    """
    if not value or not value.strip("-"):
        return "-"
    m = re.search(r"\d+(?:[.,]\d+)?", value)
    if not m:
        return _html.escape(value)
    n = int(round(float(m.group().replace(",", "."))))
    tail = n % 100
    if 11 <= tail <= 14:
        word = "месяцев"
    elif n % 10 == 1:
        word = "месяц"
    elif n % 10 in (2, 3, 4):
        word = "месяца"
    else:
        word = "месяцев"
    return f"{n} {word}"


def _esc(v: object) -> str:
    return _html.escape(str(v or ""))


def _fmt(v: float | None) -> str:
    if not v:
        return "0 ₽"
    return f"{int(round(v)):,}".replace(",", " ") + " ₽"


def _attr(product, *subs: str) -> str | None:
    for k, v in (product.attributes or {}).items():
        if any(s in k.lower() for s in subs):
            return v
    return None


def _char_rows(product) -> str:
    """Строки «Характеристик» в порядке документа сайта (см. _ATTR_PRIORITY)."""
    usable: list[tuple[str, str]] = []
    for k, v in (product.attributes or {}).items():
        low = k.lower()
        if any(sub in low for sub in _HIDE_ATTR_SUBS):
            continue
        val = (v or "").replace("\n", ", ").strip()
        if val:
            usable.append((k, val))

    def rank(pair: tuple[str, str]) -> int:
        low = pair[0].lower()
        for i, pref in enumerate(_ATTR_PRIORITY):
            if low.startswith(pref) or pref in low:
                return i
        return len(_ATTR_PRIORITY)

    # Приоритетные — в заданном порядке, остальные — как пришли из каталога
    # (там они идут по категории: конфорки, программы, таймер…).
    ordered = sorted(usable, key=lambda p: (rank(p), usable.index(p)))
    return "".join(
        f'<tr><td class="k">{_esc(k)}</td><td class="v">{_esc(v)}</td></tr>'
        for k, v in ordered[:_MAX_ATTRS]
    )


def _photo(product) -> str:
    url = getattr(product, "image_url", None)
    if url:
        return f'<div class="ph"><img src="{_esc(url)}" alt=""></div>'
    return '<div class="ph"></div>'


def _price_block(product) -> str:
    disc = ""
    if product.old_price and product.old_price > (product.price or 0):
        pct = product.discount_pct or round((1 - (product.price or 0) / product.old_price) * 100, 2)
        disc = (f'<div class="disc">Скидка -{pct:g}%</div>'
                f'<div class="old">{_fmt(product.old_price)}</div>')
    return (f'<div class="price"><span class="qty">1 шт</span>'
            f'<div class="pv">{disc}<div class="sum">{_fmt(product.price)}</div></div></div>')


def _card(item) -> str:
    p = item.product
    art = _attr(p, "артикул")
    title = _esc(p.title.upper()) + (f" ({_esc(art)})" if art else "")
    country = _attr(p, "страна производ") or "-"
    warranty = _warranty_months(_attr(p, "гарант"))
    # Ссылки «Инструкция/Схема» ведут на карточку товара: отдельных URL файлов в
    # каталоге нет, а мёртвая ссылка в документе хуже отсутствующей.
    doc_href = _esc(p.url or "")
    return f"""
    <div class="card">
      <div class="card__title">{title}</div>
      <div class="card__body">
        {_photo(p)}
        <div class="chars"><div class="chars__h">Характеристики</div>
          <table>{_char_rows(p)}</table></div>
        {_price_block(p)}
      </div>
      <div class="card__foot">
        <div class="dl"><div class="dl__t">Ссылки для скачивания</div>
          <a href="{doc_href}">Инструкция</a><a href="{doc_href}">Схема</a></div>
        <div class="meta"><div class="meta__ic">{_flag(country)}</div>
          <div><div class="meta__t">Страна<br>производства</div><b>{_esc(country)}</b></div></div>
        <div class="meta"><div class="meta__ic">{_warranty_icon()}</div>
          <div><div class="meta__t">Срок гарантии</div><b>{_esc(warranty)}</b></div></div>
      </div>
    </div>"""


def _total_block(selection: SelectionResult, settings, combo) -> str:
    subtotal = combo.subtotal if combo is not None else selection.total_price
    disc = combo.discount_total if (combo and combo.applied and not combo.gift_title) else 0.0
    final = combo.final_total if combo is not None else subtotal
    # «Индивидуальная скидка» показываем всегда (как в оригинале корзины): с бейджом
    # −X% и суммой, если скидка есть; иначе «0 ₽».
    disc_row = ""
    if combo is not None:
        pct = disc / subtotal * 100 if subtotal else 0.0
        badge = f'<span class="badge">Скидка -{pct:.2f}%</span>' if disc else ""
        disc_row = (f'<div class="trow"><span>Индивидуальная скидка</span>'
                    f'<span class="tval red">{badge}{_fmt(disc)}</span></div>')
    gift_row = ""
    if combo and combo.applied and combo.gift_title:
        gift_row = (f'<div class="trow"><span>В подарок: {_esc(combo.gift_title)}</span>'
                    f'<span class="tval">бесплатно</span></div>')

    # Шоурумы ТОЛЬКО города документа: в эталоне под «Корзина от … (г. Екатеринбург)»
    # перечислены три екатеринбургских адреса, и текст рядом обещает «в 3-х
    # шоурумах». Печатать здесь Тюмень — обещать клиенту то, чего в его городе нет.
    doc_city = (getattr(settings, "pdf_city", "") or "").strip().lower()
    matched_rooms = [lst for city, lst in SHOWROOMS if city.lower() == doc_city]
    city_rooms = matched_rooms[0] if matched_rooms else SHOWROOMS[0][1]
    rooms = "".join(
        f'<div class="room">{_IC["pin"]()}<span>{_esc(r.split(" · ")[0])}</span></div>'
        for r in city_rooms
    )
    mgr_name = _esc(settings.manager_name or "Менеджер")
    mgr = f"""
      <div class="mgr">
        <div class="mgr__l"><div class="mgr__ava"></div>
          <div><div class="mgr__name">{mgr_name}</div>
            <div class="mgr__row"><span>Почта:</span> {_esc(settings.manager_email)}</div>
            <div class="mgr__row"><span>Телефон:</span> {_esc(settings.manager_phone)}</div>
            <div class="mgr__row"><span>Городской телефон:</span> {_esc(settings.manager_city_phone)}</div>
          </div></div>
        <div class="mgr__r"><span class="city-badge">{_esc(settings.pdf_city)}</span>{rooms}</div>
      </div>"""
    return f"""
    <div class="total">
      <div class="total__h">Итого</div>
      <div class="trow"><span>Сумма без скидки</span><span class="tval muted">{_fmt(subtotal)}</span></div>
      {disc_row}{gift_row}
      <div class="trow final"><span>Сумма со скидкой</span><span class="tval big">{_fmt(final)}</span></div>
      <div class="total__note">Предложение актуально на момент формирования {datetime.now():%d.%m.%Y}</div>
      <div class="serv"><div class="serv__ic">{_IC['truck']()}</div>
        <div>Можно оформить <b>доставку</b> этого комплекта до дома с <b>подъемом</b> до квартиры</div></div>
      <div class="serv"><div class="serv__ic">{_IC['box']()}</div>
        <div>Можно <b>хранить</b> товар на складе до монтажа <b>бесплатно</b> в течение 90 календарных дней</div></div>
      <div class="serv"><div class="serv__ic">{_IC['list']()}</div>
        <div>Можно технику посмотреть в {len(city_rooms)}-х шоурумах. А также в режиме <b>онлайн</b> с помощью менеджера</div></div>
      {mgr}
    </div>
    <div class="watermark">ekrom</div>"""


def build_cart_html(selection: SelectionResult, settings, combo=None) -> str:
    """Собрать HTML-страницу комплекта в вёрстке корзины ekrom (для Chromium→PDF)."""
    cards = "".join(_card(it) for it in selection.matched)
    phone = _esc(settings.pdf_header_phone)
    city = _esc(settings.pdf_city)
    stamp = f"{datetime.now():%d.%m.%Y %H:%M}"
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<style>
{_font_css()}
*{{margin:0;padding:0;box-sizing:border-box}}
@page{{size:A4;margin:0}}
/* Кегли и координаты выставлены по МЕТРИКАМ эталонного файла сайта (извлечены из
   PDF: поле слева 15pt, заголовок товара 15pt, «Характеристики» 10.5pt, строки
   9pt, цена 13.5pt, ссылки 8.2pt, подзаголовок 7.5pt), поэтому размеры в pt. */
body{{font-family:'PangramSans','Rubik',sans-serif;color:#12232c;font-size:9pt;font-weight:500;
  -webkit-print-color-adjust:exact;print-color-adjust:exact}}
.top{{background:#13232c;height:47pt;display:flex;align-items:center;justify-content:space-between;padding:0 15pt;margin-bottom:12pt}}
.logo-svg{{height:15pt;width:auto;fill:#fff}}
.phone{{color:#fff;font-size:13.5pt;font-weight:600;text-decoration:underline;text-underline-offset:3px}}
.wrap{{padding:0 15pt}}
.subtitle{{color:#8a8a8a;font-size:7.5pt;margin-bottom:14pt}}
/* Карточка и блок «Итого» не разрываются между страницами: в документе сайта
   товар всегда целиком на одной странице, а у нас цена уезжала на предыдущую,
   футер — на следующую. */
.card{{border-bottom:1px solid #e6e6e6;padding-bottom:16px;margin-bottom:16px;
  page-break-inside:avoid;break-inside:avoid}}
.total{{page-break-inside:avoid;break-inside:avoid}}
.card__title{{font-weight:600;font-size:15pt;line-height:1.5;letter-spacing:0;margin-bottom:11pt}}
.card__body{{display:flex;gap:28pt}}
/* Сетка эталона: фото 15…~185pt, характеристики с 203pt, цена справа до 580pt. */
.ph{{width:160pt;height:160pt;border:1px solid #e6e6e6;border-radius:14pt;flex:none;display:flex;align-items:center;justify-content:center;overflow:hidden}}
.ph img{{max-width:88%;max-height:88%;object-fit:contain}}
.chars{{flex:1}}
.chars__h{{font-weight:600;font-size:10.5pt;margin-bottom:6pt}}
.chars table{{width:100%;border-collapse:collapse}}
.chars td{{padding:2.4pt 0;font-size:9pt;vertical-align:top;line-height:1.15}}
.chars .k{{color:#8a8a8a;width:52%;padding-right:8pt}}
.chars .v{{color:#12232c}}
/* Цена в эталоне стоит по ЦЕНТРУ блока характеристик (проверено по координатам
   обоих товаров), а не на фиксированном отступе сверху. */
.price{{width:104pt;flex:none;display:flex;align-items:center;justify-content:flex-end;gap:8pt}}
.price .qty{{color:#12232c;font-size:9pt;font-weight:600;padding-top:3pt;white-space:nowrap}}
.pv{{text-align:right}}
.disc{{color:#e30613;font-weight:600;font-size:8.5pt}}
.old{{color:#8a8a8a;text-decoration:line-through;font-size:8.5pt}}
.sum{{font-weight:600;font-size:13.5pt;white-space:nowrap}}
/* Футер карточки по эталону: «Страна производства» с 190pt, «Срок гарантии» с 336pt. */
.card__foot{{display:flex;gap:0;margin-top:12pt;align-items:center}}
.dl{{width:142pt}}
.card__foot .meta{{width:146pt}}
.dl__t{{font-size:9pt;margin-bottom:6pt;padding-left:19pt}}
/* Шаг между ссылками — по эталону: «Инструкция» с 15pt, «Схема» со 118.9pt. */
.dl a{{color:#12232c;text-decoration:underline;text-underline-offset:3px;font-size:8.2pt;font-weight:600;
  display:inline-block;white-space:nowrap}}
.dl a:first-of-type{{width:104pt}}
.meta{{display:flex;align-items:center;gap:9pt}}
.meta__ic{{width:24pt;text-align:center}}
.ic-warranty{{width:19pt;height:19pt;display:inline-block;vertical-align:middle}}
.flag svg{{vertical-align:middle}}
.flag--none{{display:inline-block;width:16pt;height:16pt;border-radius:50%;background:#e6e6e6}}
.meta__t{{color:#8a8a8a;font-size:9pt;line-height:1.2}}
.meta b{{font-size:9pt;font-weight:600}}
.total{{padding:10pt 15pt 0}}
.total__h{{text-align:center;font-weight:600;font-size:10.5pt;padding-bottom:9pt;border-bottom:1px solid #e6e6e6;margin-bottom:4pt}}
.trow{{display:flex;justify-content:space-between;align-items:center;padding:7pt 0;border-bottom:1px solid #f0f0f0;font-size:10.5pt}}
.trow .tval{{font-weight:600}}
.trow .muted{{color:#8a8a8a}}
.trow .red{{color:#e30613}}
.badge{{display:inline-block;border:1px solid #e30613;border-radius:9pt;color:#e30613;font-size:7.5pt;padding:1.5pt 6pt;margin-right:8pt;font-weight:500}}
.trow.final{{border-bottom:none;padding-top:9pt}}
.trow.final span:first-child{{font-weight:600;font-size:12pt}}
.trow .big{{font-weight:600;font-size:15pt}}
.total__note{{color:#8a8a8a;font-size:7.5pt;text-align:center;margin:3pt 0 12pt}}
.serv{{display:flex;align-items:center;gap:12pt;margin-bottom:9pt;font-size:9.5pt;color:#3a3a3a}}
.serv__ic{{width:24pt;flex:none;text-align:center}}
.serv b{{color:#12232c;font-weight:600}}
.mgr{{display:flex;gap:15pt;background:#2b3138;border-radius:11pt;padding:15pt;margin-top:11pt;color:#c9ced3}}
.mgr__l{{display:flex;gap:11pt;width:44%}}
.mgr__ava{{width:35pt;height:35pt;border-radius:50%;background:#4a5158;flex:none}}
.mgr__name{{color:#fff;font-weight:600;margin-bottom:6pt;font-size:9pt}}
.mgr__row{{font-size:7.5pt;margin-bottom:2pt}}
.mgr__row span{{color:#8a9099}}
.mgr__r{{flex:1;background:#333a41;border-radius:8pt;padding:11pt 12pt}}
/* Бейдж города над адресами — как в эталоне корзины. */
.city-badge{{display:inline-block;background:#1d5f74;color:#dff1f6;border-radius:4pt;
  font-size:6.5pt;padding:1.5pt 6pt;margin-bottom:8pt}}
.room{{display:flex;align-items:center;gap:6pt;font-size:7.5pt;margin-bottom:6pt}}
.watermark{{text-align:center;font-weight:600;font-size:90pt;color:#f2f2f2;letter-spacing:6pt;margin-top:15pt;line-height:1}}
</style></head><body>
<div class="top"><span class="logo">{_logo_svg()}</span><span class="phone">{phone}</span></div>
<div class="wrap"><div class="subtitle">Корзина от {stamp} &nbsp;(г. {city})</div>{cards}</div>
{_total_block(selection, settings, combo)}
</body></html>"""


async def render_html_pdf(html: str, out_path: Path) -> Path:
    """Отрендерить HTML в PDF через Chromium (Playwright). Формат A4, фон включён.

    Ждём `load` (не `networkidle`: внешние шрифт/фото могут «висеть» и подвешивать
    рендер), затем явно дожидаемся готовности шрифтов, чтобы Rubik применился.
    """
    from playwright.async_api import async_playwright

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="load")
            try:
                await page.wait_for_function("document.fonts.ready", timeout=6000)
            except Exception:  # noqa: BLE001 — шрифт не догрузился, рендерим как есть
                pass
            await page.pdf(path=str(out_path), format="A4", print_background=True)
        finally:
            await browser.close()
    return out_path
