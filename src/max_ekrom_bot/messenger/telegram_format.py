"""Форматирование подбора для сообщений Telegram."""

from __future__ import annotations

import html

from ..models import SelectionResult


def _fmt_price(value: float | None) -> str:
    if not value:
        return "—"
    return f"{int(round(value)):,}".replace(",", " ") + " ₽"


def format_selection(selection: SelectionResult) -> str:
    """Текст превью подбора (HTML для parse_mode=HTML)."""
    if not selection.matched and not selection.unmatched:
        return "Не удалось подобрать позиции по этому запросу."

    lines: list[str] = ["<b>Подобранные позиции</b>\n"]
    for i, item in enumerate(selection.matched, 1):
        p = item.product
        title = html.escape(p.title)
        # Название — кликабельная ссылка на карточку товара (если url есть).
        if p.url:
            title = f'<a href="{html.escape(p.url, quote=True)}">{title}</a>'
        price = _fmt_price(p.price)
        is_sub = "Предложена замена" in item.reason
        mark = " ⚠️ <i>замена</i>" if is_sub else ""
        lines.append(f"{i}. <b>{title}</b>{mark}\n   {price}")
        if is_sub and item.reason:
            short = html.escape(item.reason[:280])
            if len(item.reason) > 280:
                short += "…"
            lines.append(f"   <i>{short}</i>")

    for item in selection.unmatched:
        label = html.escape(item.spec.raw_text or item.spec.appliance.value)
        lines.append(f"\n❌ <b>Не найдено:</b> {label}")
        if item.reason:
            lines.append(f"   <i>{html.escape(item.reason)}</i>")

    if selection.matched:
        lines.append(f"\n<b>Итого:</b> {_fmt_price(selection.total_price)}")
    return "\n".join(lines)
