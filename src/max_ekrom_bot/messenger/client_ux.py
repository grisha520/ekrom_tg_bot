"""Тексты и UI для клиентского режима Telegram-бота."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_MSK = timezone(timedelta(hours=3))

from ..config import Settings
from ..models import SelectionResult
from ..pick_history import PickRecord
from .telegram_format import _fmt_price, format_selection

# Готовые запросы для кнопок «Примеры» (callback ex:0 … ex:N-1).
EXAMPLE_QUERIES: tuple[str, ...] = (
    "духовой шкаф чёрный встраиваемый",
    "посудомойка 45 см и вытяжка 60 см",
    "холодильник до 60000",
)

BTN_EXAMPLES = "💡 Примеры"
BTN_CONTACTS = "📞 Контакты"
BTN_HELP = "ℹ️ Как пользоваться"
BTN_HISTORY = "📂 Мои подборы"

_SMALLTALK = (
    "привет", "здравств", "добрый", "доброе", "hello", "hi", "hey",
    "салют", "хай", "доброго", "день", "утро", "вечер",
)
_NOISE = ("ок", "ok", "спасибо", "thanks", "👍", "🙏")


def is_smalltalk(text: str) -> bool:
    """Приветствие или пустой ввод — не гоняем через подбор."""
    t = text.strip().lower()
    if not t or len(t) <= 2:
        return True
    if t in _NOISE:
        return True
    words = t.split()
    if len(words) <= 3 and any(s in t for s in _SMALLTALK):
        return True
    return False


def smalltalk_reply(shop_name: str) -> str:
    return (
        f"👋 Рад знакомству! Я помогу подобрать технику <b>{html.escape(shop_name)}</b>.\n\n"
        "Напишите одним сообщением, что ищете — например:\n"
        "• <i>духовой шкаф чёрный встраиваемый</i>\n"
        "• <i>посудомойка 45 см</i>\n\n"
        "Или нажмите <b>💡 Примеры</b> под полем ввода."
    )


def welcome_text(settings: Settings) -> str:
    shop = html.escape(settings.shop_name)
    site = html.escape(settings.shop_site.rstrip("/"))
    return (
        f"👋 <b>Добро пожаловать в {shop}!</b>\n\n"
        "Я подберу бытовую технику по вашему описанию и пришлю <b>PDF-подбор</b> "
        "с ценами и характеристиками.\n\n"
        "<b>Как написать запрос</b>\n"
        "• одна позиция или список в одном сообщении\n"
        "• можно указать цвет, размер, бюджет, бренд\n\n"
        "<b>Примеры</b>\n"
        "• <i>духовой шкаф чёрный встраиваемый</i>\n"
        "• <i>посудомойка 45 см и вытяжка 60 см</i>\n"
        "• <i>холодильник до 60000</i>\n\n"
        "📂 Готовые подборы — кнопка <b>Мои подборы</b> или /history\n\n"
        f"🌐 Каталог: <a href=\"{site}\">{site}</a>"
    )


def examples_text() -> str:
    lines = [
        "💡 <b>Примеры запросов</b>",
        "",
        "Скопируйте или нажмите кнопку ниже:",
        "",
    ]
    for i, q in enumerate(EXAMPLE_QUERIES, 1):
        lines.append(f"{i}. <code>{html.escape(q)}</code>")
    lines += [
        "",
        "Можно добавить: <i>не белую</i>, <i>до 30000</i>, <i>кроме Indesit</i>.",
    ]
    return "\n".join(lines)


def contacts_text(settings: Settings) -> str:
    shop = html.escape(settings.shop_name)
    site = html.escape(settings.shop_site.rstrip("/"))
    contacts = html.escape(settings.shop_contacts.strip()) if settings.shop_contacts else ""
    body = f"📞 <b>Контакты {shop}</b>\n\n"
    if contacts:
        body += f"{contacts}\n\n"
    else:
        body += "Менеджер поможет уточнить наличие и оформить заказ.\n\n"
    body += f"🌐 Сайт: <a href=\"{site}\">{site}</a>\n\n"
    body += "<i>После подбора менеджер свяжется с вами при необходимости.</i>"
    return body


def examples_inline_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    labels = ("🍳 Духовка", "🧽 Посудомойка + вытяжка", "❄️ Холодильник")
    for i, label in enumerate(labels):
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"ex:{i}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _phone_url(contacts: str) -> str | None:
    m = re.search(r"(\+7[\d\s\-()]{10,})", contacts)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    if len(digits) >= 11:
        return f"tel:+{digits}" if not digits.startswith("+") else f"tel:{digits}"
    return None


def followup_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    site = settings.shop_site.rstrip("/")
    rows.append([InlineKeyboardButton(text="🌐 Сайт ekrom", url=site)])
    tel = _phone_url(settings.shop_contacts)
    if tel:
        rows.append([InlineKeyboardButton(text="📞 Позвонить", url=tel)])
    rows.append([InlineKeyboardButton(text="💡 Ещё примеры", callback_data="ex:menu")])
    rows.append([InlineKeyboardButton(text=BTN_HISTORY, callback_data="hist:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def followup_text(settings: Settings) -> str:
    return (
        "Нужна помощь с выбором или оформлением?\n"
        f"Напишите сюда или на сайте <b>{html.escape(settings.shop_name)}</b>.\n\n"
        "<i>Цены и наличие на момент заказа уточняет менеджер.</i>"
    )


def format_client_selection(selection: SelectionResult, shop_name: str) -> str:
    """Клиентское превью: теплее операторского, с дисклеймером."""
    if not selection.matched and not selection.unmatched:
        return (
            "😔 Не получилось разобрать запрос.\n\n"
            "Попробуйте так: <i>духовой шкаф чёрный</i> или нажмите 💡 Примеры."
        )

    base = format_selection(selection)
    n = len(selection.matched)
    header = f"✅ <b>Ваш подбор готов</b> · {n} {_plural_pos(n)}\n\n"
    footer = (
        f"\n\n<i>ℹ️ Автоподбор {html.escape(shop_name)}. "
        "Точные цены и наличие подтвердит менеджер.</i>"
    )
    if selection.matched and selection.unmatched:
        footer = (
            f"\n\n⚠️ <i>Часть позиций не найдена — менеджер поможет доработать подбор.</i>"
            + footer
        )
    elif not selection.matched:
        return base  # только «не найдено»

    # Заменяем заголовок операторского превью на клиентский.
    if base.startswith("<b>Подобранные позиции</b>"):
        base = base.replace("<b>Подобранные позиции</b>", "<b>Что подобрали</b>", 1)
    return header + base + footer


def _plural_pos(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "позиция"
    if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20):
        return "позиции"
    return "позиций"


def client_status_parse() -> str:
    return (
        "🔍 <b>Разбираю запрос…</b>\n"
        "<i>Обычно до минуты — подождите, пожалуйста.</i>"
    )


def client_status_select() -> str:
    return (
        "📦 <b>Подбираю товары…</b>\n"
        "<i>Ищу подходящие варианты в каталоге ekrom.</i>"
    )


def client_status_pdf() -> str:
    return "📄 <b>Готовлю PDF…</b>\n<i>Почти готово!</i>"


def empty_result_text() -> str:
    return (
        "😔 К сожалению, по этому запросу ничего не нашлось.\n\n"
        "Попробуйте:\n"
        "• переформулировать (другой размер или бюджет)\n"
        "• нажать <b>💡 Примеры</b>\n"
        "• написать менеджеру — поможем вручную"
    )


def pdf_caption() -> str:
    return "📄 Ваш подбор в PDF — удобно сохранить или переслать"


def _fmt_msk(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=_MSK).strftime("%d.%m %H:%M")


def _short_query(text: str, max_len: int = 40) -> str:
    t = text.replace("\n", " ").strip()
    return t if len(t) <= max_len else t[: max_len - 1] + "…"


def format_history_list(records: list[PickRecord]) -> str:
    if not records:
        return (
            "📂 <b>Мои подборы</b>\n\n"
            "Пока пусто — после первого подбора с PDF записи появятся здесь."
        )
    lines = [
        "📂 <b>Мои подборы</b>",
        "",
        "Нажмите кнопку, чтобы снова получить PDF:",
        "",
    ]
    for i, rec in enumerate(records, 1):
        date = _fmt_msk(rec.created_at)
        q = html.escape(_short_query(rec.query_text))
        price = _fmt_price(rec.total_price)
        lines.append(f"{i}. <b>{date}</b> · {q}\n   {price}")
    lines.append("\n<i>Храним последние 20 подборов за 30 дней.</i>")
    return "\n".join(lines)


def history_inline_keyboard(records: list[PickRecord]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for rec in records:
        label = f"📄 {_fmt_msk(rec.created_at)} · {_short_query(rec.query_text, 22)}"
        rows.append([
            InlineKeyboardButton(text=label[:64], callback_data=f"hist:{rec.id}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Ценовые уровни комплекта (бюджет / оптимум / премиум) ─────────────────────

# Порядок = порядок табов. optimal — «как сейчас» (сегмент запроса клиента).
TIER_ORDER: tuple[str, ...] = ("budget", "optimal", "premium")
TIER_TITLES: dict[str, str] = {
    "budget": "💰 Бюджетный",
    "optimal": "⭐ Оптимальный",
    "premium": "💎 Премиум",
}
TIER_PDF_LABELS: dict[str, str] = {
    "budget": "Бюджетный вариант",
    "optimal": "Оптимальный вариант",
    "premium": "Премиум-вариант",
}


def tier_keyboard(token: str, active: str) -> InlineKeyboardMarkup:
    """Табы уровней (актив помечен галочкой) + кнопка PDF активного уровня."""
    tabs = [
        InlineKeyboardButton(
            text=("✓ " if lvl == active else "") + TIER_TITLES[lvl],
            callback_data=f"tier:{token}:{lvl}",
        )
        for lvl in TIER_ORDER
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            tabs,
            [InlineKeyboardButton(
                text="📄 Скачать PDF этого варианта",
                callback_data=f"tierpdf:{token}",
            )],
        ]
    )
