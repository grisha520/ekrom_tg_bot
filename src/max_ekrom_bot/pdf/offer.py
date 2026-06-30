"""Свой генератор PDF-подбора для клиента.

Рисуем PDF из УЖЕ собранных данных подбора (`SelectionResult`): брендинг магазина,
позиции с ценой и ключевыми характеристиками, итог, дисклеймер. В отличие от старого
`Pipeline.build_pdf` (который наполнял общую партнёрскую корзину ekrom и скачивал
`cart_pdf.php`), этот модуль:

  • не ходит в ekrom и не зависит от сессии/корзины — значит формируется параллельно
    и не упирается в единственный сеанс (узкое место при потоке клиентов);
  • показывает клиенту наш брендинг и нужные цены, а не партнёрский документ.

Шрифт: нужен TTF с кириллицей. Берём `PDF_FONT_PATH`, иначе ищем системный
(DejaVuSans на Linux-сервере / Arial на Windows).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..models import SelectionResult

# Характеристики, которые клиенту не показываем (служебное/внутреннее).
_HIDE_ATTRS = {
    "промо",
    "остаток поставщика",
    "артикул",
    "производитель",
    "модель",
    "страна производитель",
}
_MAX_ATTRS = 6  # сколько характеристик максимум выводим на позицию
_FONT = "Body"
_FONT_BOLD = "Body-Bold"
_registered = False

# Кандидаты системных шрифтов (regular, bold).
_FONT_CANDIDATES_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/Library/Fonts/Arial.ttf",
)
_FONT_CANDIDATES_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)


def _first_existing(*paths: str) -> str | None:
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def _register_font(font_path: str = "") -> None:
    """Регистрируем TTF-семейство с кириллицей (один раз на процесс)."""
    global _registered
    if _registered:
        return
    regular = _first_existing(font_path, *_FONT_CANDIDATES_REGULAR)
    if not regular:
        raise RuntimeError(
            "Не найден TTF-шрифт с кириллицей для PDF. Укажите PDF_FONT_PATH "
            "(например путь к DejaVuSans.ttf или arial.ttf)."
        )
    bold = _first_existing(*_FONT_CANDIDATES_BOLD) or regular
    pdfmetrics.registerFont(TTFont(_FONT, regular))
    pdfmetrics.registerFont(TTFont(_FONT_BOLD, bold))
    pdfmetrics.registerFontFamily(_FONT, normal=_FONT, bold=_FONT_BOLD)
    _registered = True


def _fmt_price(value: float | None) -> str:
    if not value:
        return "—"
    return f"{int(round(value)):,}".replace(",", " ") + " ₽"  # ₽


def _attrs_line(attrs: dict[str, str]) -> str:
    """Короткая строка ключевых характеристик товара для клиента."""
    parts: list[str] = []
    for key, val in (attrs or {}).items():
        if key.lower() in _HIDE_ATTRS:
            continue
        clean = (val or "").replace("\n", ", ").strip().strip(",")
        if not clean:
            continue
        parts.append(f"{key}: {clean}")
        if len(parts) >= _MAX_ATTRS:
            break
    return "  ·  ".join(parts)  # ·


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName=_FONT, fontSize=10, leading=13)
    return {
        "base": base,
        "title": ParagraphStyle(
            "title", parent=base, fontName=_FONT_BOLD, fontSize=18, leading=22
        ),
        "sub": ParagraphStyle(
            "sub", parent=base, fontSize=9, textColor=colors.HexColor("#666666")
        ),
        "item": ParagraphStyle(
            "item", parent=base, fontName=_FONT_BOLD, fontSize=11, leading=14
        ),
        "attrs": ParagraphStyle(
            "attrs", parent=base, fontSize=8.5, leading=11,
            textColor=colors.HexColor("#555555"),
        ),
        "price": ParagraphStyle(
            "price", parent=base, fontName=_FONT_BOLD, fontSize=11,
            alignment=TA_RIGHT,
        ),
        "total": ParagraphStyle(
            "total", parent=base, fontName=_FONT_BOLD, fontSize=13,
            alignment=TA_RIGHT,
        ),
        "foot": ParagraphStyle(
            "foot", parent=base, fontSize=8, alignment=TA_CENTER,
            textColor=colors.HexColor("#888888"),
        ),
    }


def build_offer_pdf(
    selection: SelectionResult, settings, out_path: str | Path | None = None
) -> Path:
    """Сформировать клиентский PDF-подбор и вернуть путь к файлу."""
    _register_font(getattr(settings, "pdf_font_path", "") or "")
    st = _styles()

    if out_path is None:
        out_dir = settings.output_path
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"podbor_{stamp}.pdf"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Подбор техники",
        author=settings.shop_name,
    )

    story: list = []

    # ── Шапка: лого (если есть) + название магазина + дата ───────────────────
    logo_path = getattr(settings, "pdf_logo_path", "") or ""
    if logo_path and os.path.exists(logo_path):
        try:
            img = Image(logo_path)
            img._restrictSize(45 * mm, 22 * mm)
            story.append(img)
            story.append(Spacer(1, 4 * mm))
        except Exception:  # noqa: BLE001 — лого не критично
            pass

    story.append(Paragraph(settings.shop_name, st["title"]))
    story.append(
        Paragraph(
            f"Подбор техники · {datetime.now():%d.%m.%Y}", st["sub"]
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#dddddd")))
    story.append(Spacer(1, 4 * mm))

    # ── Позиции ──────────────────────────────────────────────────────────────
    rows: list[list] = []
    for i, item in enumerate(selection.matched, 1):
        p = item.product
        name_cell = [Paragraph(p.title, st["item"])]
        attrs = _attrs_line(p.attributes)
        if attrs:
            name_cell.append(Paragraph(attrs, st["attrs"]))
        rows.append(
            [Paragraph(str(i), st["base"]), name_cell,
             Paragraph(_fmt_price(p.price), st["price"])]
        )

    if rows:
        table = Table(rows, colWidths=[8 * mm, 132 * mm, 34 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#eeeeee")),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 4 * mm))
        story.append(HRFlowable(width="100%", color=colors.HexColor("#dddddd")))
        story.append(Spacer(1, 3 * mm))
        story.append(
            Paragraph(f"Итого: {_fmt_price(selection.total_price)}", st["total"])
        )
    else:
        story.append(
            Paragraph("По вашему запросу подходящих позиций не найдено.", st["base"])
        )

    # ── Подвал: контакты + дисклеймер ────────────────────────────────────────
    story.append(Spacer(1, 10 * mm))
    foot_bits = [b for b in (settings.shop_contacts, settings.shop_site) if b]
    if foot_bits:
        story.append(Paragraph("  ·  ".join(foot_bits), st["foot"]))
    story.append(
        Paragraph(
            "Цены и наличие ориентировочные, актуальные уточняйте у менеджера.",
            st["foot"],
        )
    )

    doc.build(story)
    return out_path
