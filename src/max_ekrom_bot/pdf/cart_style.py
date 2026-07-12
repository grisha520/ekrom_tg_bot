"""Генератор PDF-комплекта в стиле документа корзины ekrom (эталон).

Визуально повторяет `cart_pdf.php`: шапка «Корзина от …», по каждому товару —
фото + название + «Характеристики» + цена (со скидкой, если есть) + ссылки/страна/
гарантия, и блок «Итого» со скидкой на комплект + карточка менеджера и шоурумы.

Данные берём из уже собранного `SelectionResult` (товары с attributes/image_url и,
для акционных, old_price). Итоговую скидку на комплект передаём отдельно.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..models import SelectionResult
from .offer import _fetch_image, _register_font, _FONT, _FONT_BOLD

if TYPE_CHECKING:
    from ..matching.combo_pricing import ComboResult

_INK = colors.HexColor("#1a1a1a")
_MUTED = colors.HexColor("#777777")
_LINE = colors.HexColor("#dddddd")
_RED = colors.HexColor("#d0021b")
_BOX = colors.HexColor("#f3f3f3")

# Характеристики карточки: как в эталоне (артикул/цвет/модель/страна/гарантия/…).
# Показываем все имеющиеся, но служебный шум прячем.
_HIDE = {"промо", "остаток поставщика", "производитель", "страна производитель"}


def _fmt(v: float | None) -> str:
    if not v:
        return "0 ₽"
    return f"{int(round(v)):,}".replace(",", " ") + " ₽"


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("b", fontName=_FONT, fontSize=8.5, leading=11, textColor=_INK)
    return {
        "base": base,
        "phone": ParagraphStyle("ph", parent=base, fontName=_FONT_BOLD, fontSize=11),
        "sub": ParagraphStyle("s", parent=base, fontSize=9, textColor=_MUTED),
        "title": ParagraphStyle("t", parent=base, fontName=_FONT_BOLD, fontSize=9.5, leading=12),
        "h": ParagraphStyle("h", parent=base, fontName=_FONT_BOLD, fontSize=8.5),
        "aname": ParagraphStyle("an", parent=base, textColor=_MUTED),
        "price": ParagraphStyle("p", parent=base, fontName=_FONT_BOLD, fontSize=11, alignment=TA_RIGHT),
        "old": ParagraphStyle("o", parent=base, fontSize=9, alignment=TA_RIGHT, textColor=_MUTED),
        "disc": ParagraphStyle("d", parent=base, fontName=_FONT_BOLD, fontSize=9, alignment=TA_RIGHT, textColor=_RED),
        "box": ParagraphStyle("bx", parent=base, fontSize=7.5, textColor=_MUTED, leading=9),
        "total_l": ParagraphStyle("tl", parent=base, fontSize=10),
        "total_v": ParagraphStyle("tv", parent=base, fontName=_FONT_BOLD, fontSize=10, alignment=TA_RIGHT),
        "big": ParagraphStyle("bg", parent=base, fontName=_FONT_BOLD, fontSize=13),
        "bigv": ParagraphStyle("bv", parent=base, fontName=_FONT_BOLD, fontSize=13, alignment=TA_RIGHT),
    }


def _photo(url: str | None):
    data = _fetch_image(url)
    if not data:
        return ""
    try:
        img = Image(io.BytesIO(data))
        img._restrictSize(34 * mm, 34 * mm)
        return img
    except Exception:  # noqa: BLE001
        return ""


def _attr(product, *subs) -> str | None:
    for k, v in (product.attributes or {}).items():
        if any(s in k.lower() for s in subs):
            return v
    return None


def _char_rows(product, st) -> list[list]:
    rows: list[list] = []
    for k, v in (product.attributes or {}).items():
        if k.lower() in _HIDE:
            continue
        val = (v or "").replace("\n", ", ").strip()
        if not val:
            continue
        rows.append([Paragraph(k, st["aname"]), Paragraph(val, st["base"])])
    return rows


def _product_block(item, st) -> KeepTogether:
    import html as _html

    p = item.product
    title = _html.escape(p.title.upper())
    art = _attr(p, "артикул")
    title_line = f"{title}" + (f" ({_html.escape(art)})" if art else "")

    right: list = [Paragraph(title_line, st["title"]), Spacer(1, 1.5 * mm)]
    # Блок «Характеристики» рисуем только если они есть: reportlab падает на
    # Table([]) («must have at least a row»), а у товара без обогащения атрибутов нет.
    char_rows = _char_rows(p, st)
    if char_rows:
        right.append(Paragraph("Характеристики", st["h"]))
        right.append(Spacer(1, 0.8 * mm))
        ct = Table(char_rows, colWidths=[42 * mm, 58 * mm])
        ct.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 0.6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0.6),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))
        right.append(ct)

    # Цена (со скидкой если есть).
    right.append(Spacer(1, 1.5 * mm))
    price_cell: list = []
    if p.old_price and p.old_price > (p.price or 0):
        pct = p.discount_pct or round((1 - (p.price or 0) / p.old_price) * 100, 2)
        # Дробный % как в эталоне корзины ekrom («Скидка -68.22%», «-100%»).
        price_cell.append(Paragraph(f"Скидка -{pct:g}%", st["disc"]))
        price_cell.append(Paragraph(f'<strike>{_fmt(p.old_price)}</strike>', st["old"]))
    prow = Table(
        [[Paragraph("1 шт", st["base"])] + [price_cell + [Paragraph(_fmt(p.price), st["price"])]]],
        colWidths=[70 * mm, 30 * mm],
    )
    prow.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    right.append(prow)

    # Страна / гарантия — в две «плитки».
    country = _attr(p, "страна производ") or "—"
    warranty = _attr(p, "гарант")
    warranty = f"{warranty} месяцев" if warranty else "—"
    boxes = Table(
        [[Paragraph(f"<b>Страна производства</b><br/>{_html.escape(country)}", st["box"]),
          Paragraph(f"<b>Срок гарантии</b><br/>{_html.escape(warranty)}", st["box"])]],
        colWidths=[50 * mm, 50 * mm],
    )
    boxes.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _BOX), ("BOX", (0, 0), (-1, -1), 0.4, _LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, _LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    right.append(Spacer(1, 1.5 * mm))
    right.append(boxes)

    row = Table([[_photo(p.image_url), right]], colWidths=[38 * mm, 104 * mm])
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, _LINE),
    ]))
    return KeepTogether([row])


def build_cart_style_pdf(
    selection: SelectionResult, settings, out_path: str | Path | None = None,
    combo_discount_pct: float | None = None, promo_note: str | None = None,
    combo: "ComboResult | None" = None,
) -> Path:
    """Собрать PDF-комплект в стиле корзины ekrom.

    combo — результат движка расчёта (`matching.combo_pricing.compute_combo`): даёт
    подытог, размер скидки (плоский % ИЛИ рубли на наименьший товар при каскаде),
    пометку и флаг «итог уточнит менеджер». Если передан — он ведёт блок «Итого».
    combo_discount_pct/promo_note — легаси-путь (единый % и текст), когда движок не
    используется.
    """
    _register_font(getattr(settings, "pdf_font_path", "") or "")
    st = _styles()
    if out_path is None:
        out_dir = settings.output_path
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"komplekt_{datetime.now():%Y%m%d_%H%M%S}.pdf"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=13 * mm, bottomMargin=13 * mm, title="Подбор комплекта",
    )
    story: list = [
        Paragraph(settings.pdf_header_phone, st["phone"]),
        Paragraph(f"Комплект от {datetime.now():%d.%m.%Y  %H:%M}  (г. {settings.pdf_city})", st["sub"]),
        Spacer(1, 3 * mm),
        HRFlowable(width="100%", color=_LINE),
    ]
    for item in selection.matched:
        story.append(_product_block(item, st))

    # ── Итого ────────────────────────────────────────────────────────────────
    subtotal = sum((it.product.price or 0) for it in selection.matched)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Итого", st["big"]))
    story.append(Spacer(1, 2 * mm))

    # Скидку/подарок/пометку берём из движка (combo), иначе — из легаси-параметров.
    disc_amount = 0.0
    disc_label: str | None = None
    gift_line: tuple[str, float] | None = None
    note = promo_note
    manager_review = False
    final: float | None = None
    if combo is not None:
        subtotal = combo.subtotal
        note = combo.note or promo_note
        manager_review = combo.manager_review
        final = combo.final_total
        if combo.applied and combo.gift_title and combo.final_total >= combo.subtotal:
            # Подарок СВЕРХУ (модель не входит в позиции) — сумму не уменьшает.
            gift_line = (combo.gift_title, combo.discount_total)
        elif combo.applied and combo.discount_total:
            # Формат эталонного PDF корзины: «Индивидуальная скидка −X%  <рубли>»,
            # где X — общий % от суммы без скидки (спеццена/подарок-вычет/каскад/%).
            disc_amount = combo.discount_total
            pct = combo.discount_total / combo.subtotal * 100 if combo.subtotal else 0.0
            disc_label = f"Индивидуальная скидка −{pct:.2f}%".replace(".00%", "%")
    elif combo_discount_pct:
        disc_amount = subtotal * combo_discount_pct / 100
        disc_label = f"Скидка на комплект −{combo_discount_pct:.2f}%".rstrip("0").rstrip(".")

    rows = [[Paragraph("Сумма без скидки", st["total_l"]), Paragraph(_fmt(subtotal), st["total_v"])]]
    if disc_label and disc_amount:
        rows.append([Paragraph(disc_label, st["total_l"]),
                     Paragraph("−" + _fmt(disc_amount), st["total_v"])])
    if gift_line:
        g_title, g_price = gift_line
        rows.append([Paragraph(f"В подарок: {g_title} (стоимость {_fmt(g_price)})", st["total_l"]),
                     Paragraph("бесплатно", st["total_v"])])
    if final is None:
        final = subtotal - disc_amount
    final_label = "Сумма к оплате" if (gift_line and not disc_amount) else "Сумма со скидкой"
    tt = Table(rows, colWidths=[130 * mm, 50 * mm])
    tt.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story.append(tt)
    story.append(Spacer(1, 1.5 * mm))
    final_t = Table([[Paragraph(final_label, st["big"]), Paragraph(_fmt(final), st["bigv"])]],
                    colWidths=[130 * mm, 50 * mm])
    final_t.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 1, _INK), ("TOPPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(final_t)
    if note:
        story.append(Spacer(1, 1 * mm))
        story.append(Paragraph(note, st["sub"]))
    if manager_review:
        story.append(Spacer(1, 0.5 * mm))
        story.append(Paragraph(
            "Итоговая цена комплекта по этой акции — после подтверждения менеджером.",
            st["sub"],
        ))
    story.append(Paragraph(f"Предложение актуально на {datetime.now():%d.%m.%Y}", st["box"]))

    # Сервис + менеджер + шоурумы (как в эталоне).
    story.append(Spacer(1, 4 * mm))
    for line in (
        "Можно оформить доставку комплекта до дома с подъёмом до квартиры",
        "Можно хранить товар на складе до монтажа бесплатно в течение 90 дней",
        "Можно посмотреть технику в 3 шоурумах или онлайн с менеджером",
    ):
        story.append(Paragraph("• " + line, st["base"]))

    if settings.manager_name or settings.manager_phone:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(f"<b>{settings.manager_name or 'Менеджер'}</b>", st["base"]))
        if settings.manager_email:
            story.append(Paragraph(f"Почта: {settings.manager_email}", st["base"]))
        if settings.manager_phone:
            story.append(Paragraph(f"Телефон: {settings.manager_phone}", st["base"]))
        story.append(Paragraph(f"Городской телефон: {settings.manager_city_phone}", st["base"]))

    from ..messenger.client_ux import _SHOWROOMS
    story.append(Spacer(1, 2 * mm))
    for _city, rooms in _SHOWROOMS:
        for room in rooms:
            story.append(Paragraph("• " + room.split(" · ")[0], st["box"]))

    doc.build(story)
    return out_path
