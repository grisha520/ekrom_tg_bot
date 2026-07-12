"""PDF-комплект «точь-в-точь» как корзина ekrom — через HTML+CSS → Chromium.

Оригинал `cart_pdf.php` сайт рендерит из HTML браузерным движком, поэтому reportlab
не даёт пиксельной идентичности. Здесь мы верстаем ТУ ЖЕ разметку (шрифт Rubik,
логотип EKROM, тёмная шапка, карточки товаров, блок «Итого», карточка менеджера,
шоурумы, водяной знак) и рендерим уже встроенным Playwright/Chromium (`page.pdf`),
подставляя НАШИ данные и посчитанную комплектную скидку (которой нет в корзине сайта).

Точка входа: `build_cart_html(selection, settings, combo)` → HTML (чистая, тестируемая);
`render_html_pdf(html, out_path)` — async-рендер. Итог собирает `Pipeline.build_kit_pdf`.
"""

from __future__ import annotations

import html as _html
from datetime import datetime
from pathlib import Path

from ..models import SelectionResult

_ASSETS = Path(__file__).parent / "assets"


def _logo_svg() -> str:
    try:
        svg = (_ASSETS / "ekrom_logo.svg").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — без логотипа не критично
        return "EKROM"
    return svg.replace("<svg ", '<svg class="logo-svg" ', 1)


# ── Флаги стран (круглые SVG; эмодзи-флаги в Chromium ненадёжны) ───────────────
def _flag(country: str | None) -> str:
    c = (country or "").strip().lower()
    key = next((k for k in _FLAGS if k in c), None)
    if key:
        return _FLAGS[key]
    return '<span class="flag flag--none"></span>'  # серый круг, как в оригинале «-»


def _round_flag(stripes: list[str], horizontal: bool = True) -> str:
    """Круглый флаг из цветных полос (упрощённо, узнаваемо на 22px)."""
    n = len(stripes)
    bars = []
    for i, col in enumerate(stripes):
        if horizontal:
            bars.append(f'<rect x="0" y="{i*24/n}" width="24" height="{24/n}" fill="{col}"/>')
        else:
            bars.append(f'<rect x="{i*24/n}" y="0" width="{24/n}" height="24" fill="{col}"/>')
    return (f'<span class="flag"><svg viewBox="0 0 24 24" width="22" height="22">'
            f'<defs><clipPath id="cf"><circle cx="12" cy="12" r="12"/></clipPath></defs>'
            f'<g clip-path="url(#cf)">{"".join(bars)}</g></svg></span>')


_FLAGS = {
    "росс": _round_flag(["#fff", "#0039A6", "#D52B1E"]),
    "китай": _round_flag(["#DE2910", "#DE2910"]),
    "кита": _round_flag(["#DE2910", "#DE2910"]),
    "турц": _round_flag(["#E30A17", "#E30A17"]),
    "герман": _round_flag(["#000", "#DD0000", "#FFCE00"]),
    "итал": _round_flag(["#009246", "#fff", "#CE2B37"], horizontal=False),
    "польш": _round_flag(["#fff", "#DC143C"]),
    "беларус": _round_flag(["#D22730", "#009A44"]),
}


# ── Иконки (inline SVG, близко к оригиналу) ───────────────────────────────────
_IC = {
    "hammer": '<svg viewBox="0 0 28 28" width="28" height="28" fill="none" stroke="#12232c" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 7l5 5M4 24l8-8M15 4l9 9-3 3-9-9zM11 15l-1 4 4-1"/></svg>',
    "truck": '<svg viewBox="0 0 28 28" width="28" height="28" fill="none" stroke="#12232c" stroke-width="1.5"><rect x="2" y="6" width="14" height="11"/><path d="M16 9h4l4 4v4h-8z"/><circle cx="8" cy="19" r="2"/><circle cx="20" cy="19" r="2"/></svg>',
    "box": '<svg viewBox="0 0 28 28" width="28" height="28" fill="none" stroke="#12232c" stroke-width="1.5"><path d="M4 9l10-5 10 5v10l-10 5-10-5z"/><path d="M4 9l10 5 10-5M14 14v10"/></svg>',
    "list": '<svg viewBox="0 0 28 28" width="28" height="28" fill="none" stroke="#12232c" stroke-width="1.5"><rect x="6" y="3" width="16" height="22" rx="2"/><path d="M10 9h8M10 14h8M10 19h5"/></svg>',
    "pin": '<svg viewBox="0 0 24 24" width="12" height="12" fill="#3b6fd4"><path d="M12 2C8 2 5 5 5 9c0 5 7 13 7 13s7-8 7-13c0-4-3-7-7-7zm0 9.5A2.5 2.5 0 1112 6a2.5 2.5 0 010 5z"/></svg>',
}

_HIDE_ATTRS = {"промо", "остаток поставщика", "производитель", "страна производитель"}
_MAX_ATTRS = 11


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
    rows = []
    for k, v in (product.attributes or {}).items():
        if k.lower() in _HIDE_ATTRS:
            continue
        val = (v or "").replace("\n", ", ").strip()
        if not val:
            continue
        rows.append(f'<tr><td class="k">{_esc(k)}</td><td class="v">{_esc(val)}</td></tr>')
        if len(rows) >= _MAX_ATTRS:
            break
    return "".join(rows)


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
    warranty = _attr(p, "гарант")
    warranty = f"{warranty} месяцев" if warranty and warranty.strip("-") else "-"
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
          <a>Инструкция</a><a>Схема</a></div>
        <div class="meta"><div class="meta__ic">{_flag(country)}</div>
          <div><div class="meta__t">Страна<br>производства</div><b>{_esc(country)}</b></div></div>
        <div class="meta"><div class="meta__ic">{_IC['hammer']}</div>
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

    from ..messenger.client_ux import _SHOWROOMS
    rooms = "".join(
        f'<div class="room">{_IC["pin"]}<span>{_esc(r.split(" · ")[0])}</span></div>'
        for _city, lst in _SHOWROOMS for r in lst
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
        <div class="mgr__r">{rooms}</div>
      </div>"""
    return f"""
    <div class="total">
      <div class="total__h">Итого</div>
      <div class="trow"><span>Сумма без скидки</span><span class="tval muted">{_fmt(subtotal)}</span></div>
      {disc_row}{gift_row}
      <div class="trow final"><span>Сумма со скидкой</span><span class="tval big">{_fmt(final)}</span></div>
      <div class="total__note">Предложение актуально на момент формирования {datetime.now():%d.%m.%Y}</div>
      <div class="serv"><div class="serv__ic">{_IC['truck']}</div>
        <div>Можно оформить <b>доставку</b> этого комплекта до дома с <b>подъемом</b> до квартиры</div></div>
      <div class="serv"><div class="serv__ic">{_IC['box']}</div>
        <div>Можно <b>хранить</b> товар на складе до монтажа <b>бесплатно</b> в течение 90 календарных дней</div></div>
      <div class="serv"><div class="serv__ic">{_IC['list']}</div>
        <div>Можно технику посмотреть в 3-х шоурумах. А также в режиме <b>онлайн</b> с помощью менеджера</div></div>
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
@import url('https://fonts.googleapis.com/css2?family=Rubik:wght@400;500;600;700&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}
@page{{size:A4;margin:0}}
body{{font-family:'Rubik',sans-serif;color:#12232c;font-size:11px;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.top{{background:#13232c;height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 34px;margin-bottom:16px}}
.logo-svg{{height:20px;width:auto;fill:#fff}}
.phone{{color:#fff;font-size:15px;font-weight:500;text-decoration:underline;text-underline-offset:3px}}
.wrap{{padding:0 34px}}
.subtitle{{color:#8a8a8a;font-size:10.5px;margin-bottom:18px}}
.card{{border-bottom:1px solid #e6e6e6;padding-bottom:16px;margin-bottom:16px}}
.card__title{{font-weight:700;font-size:16px;letter-spacing:.2px;margin-bottom:14px}}
.card__body{{display:flex;gap:22px}}
.ph{{width:200px;height:200px;border:1px solid #e6e6e6;border-radius:18px;flex:none;display:flex;align-items:center;justify-content:center;overflow:hidden}}
.ph img{{max-width:88%;max-height:88%;object-fit:contain}}
.chars{{flex:1}}
.chars__h{{font-weight:600;font-size:14px;margin-bottom:8px}}
.chars table{{width:100%;border-collapse:collapse}}
.chars td{{padding:3px 0;font-size:11.5px;vertical-align:top}}
.chars .k{{color:#8a8a8a;width:52%}}
.chars .v{{color:#12232c}}
.price{{width:170px;flex:none;display:flex;align-items:flex-start;justify-content:flex-end;gap:14px;padding-top:64px}}
.price .qty{{color:#12232c;font-size:12px;padding-top:4px}}
.pv{{text-align:right}}
.disc{{color:#e30613;font-weight:600;font-size:11px}}
.old{{color:#8a8a8a;text-decoration:line-through;font-size:11px}}
.sum{{font-weight:700;font-size:19px;white-space:nowrap}}
.card__foot{{display:flex;gap:38px;margin-top:16px;align-items:center}}
.dl{{width:200px}}
.dl__t{{font-size:11px;margin-bottom:8px}}
.dl a{{color:#12232c;text-decoration:underline;text-underline-offset:3px;margin-right:36px;font-size:11.5px}}
.meta{{display:flex;align-items:center;gap:12px}}
.meta__ic{{width:32px;text-align:center}}
.flag svg{{vertical-align:middle}}
.flag--none{{display:inline-block;width:22px;height:22px;border-radius:50%;background:#e6e6e6}}
.meta__t{{color:#8a8a8a;font-size:10.5px;line-height:1.15}}
.meta b{{font-size:12px}}
.total{{padding:14px 34px 0}}
.total__h{{text-align:center;font-weight:600;font-size:15px;padding-bottom:12px;border-bottom:1px solid #e6e6e6;margin-bottom:6px}}
.trow{{display:flex;justify-content:space-between;align-items:center;padding:9px 0;border-bottom:1px solid #f0f0f0;font-size:13px}}
.trow .tval{{font-weight:600}}
.trow .muted{{color:#8a8a8a}}
.trow .red{{color:#e30613}}
.badge{{display:inline-block;border:1px solid #e30613;border-radius:12px;color:#e30613;font-size:10px;padding:2px 8px;margin-right:10px;font-weight:500}}
.trow.final{{border-bottom:none;padding-top:12px}}
.trow.final span:first-child{{font-weight:600;font-size:15px}}
.trow .big{{font-weight:700;font-size:19px}}
.total__note{{color:#8a8a8a;font-size:10px;text-align:center;margin:4px 0 16px}}
.serv{{display:flex;align-items:center;gap:16px;margin-bottom:12px;font-size:12px;color:#3a3a3a}}
.serv__ic{{width:32px;flex:none;text-align:center}}
.serv b{{color:#12232c}}
.mgr{{display:flex;gap:20px;background:#2b3138;border-radius:14px;padding:20px;margin-top:14px;color:#c9ced3}}
.mgr__l{{display:flex;gap:14px;width:44%}}
.mgr__ava{{width:46px;height:46px;border-radius:50%;background:#4a5158;flex:none}}
.mgr__name{{color:#fff;font-weight:600;margin-bottom:8px;font-size:12px}}
.mgr__row{{font-size:10.5px;margin-bottom:3px}}
.mgr__row span{{color:#8a9099}}
.mgr__r{{flex:1;background:#333a41;border-radius:10px;padding:14px 16px}}
.room{{display:flex;align-items:center;gap:8px;font-size:10.5px;margin-bottom:8px}}
.watermark{{text-align:center;font-weight:700;font-size:120px;color:#f2f2f2;letter-spacing:8px;margin-top:20px;line-height:1}}
</style></head><body>
<div class="top"><span class="logo">{_logo_svg()}</span><span class="phone">{phone}</span></div>
<div class="wrap"><div class="subtitle">Комплект от {stamp} &nbsp;(г. {city})</div>{cards}</div>
{_total_block(selection, settings, combo)}
</body></html>"""


async def render_html_pdf(html: str, out_path: Path) -> Path:
    """Отрендерить HTML в PDF через Chromium (Playwright). Формат A4, фон включён."""
    from playwright.async_api import async_playwright

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            await page.pdf(path=str(out_path), format="A4", print_background=True)
        finally:
            await browser.close()
    return out_path
