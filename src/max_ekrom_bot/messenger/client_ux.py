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

# Готовые запросы для кнопок «Примеры» (callback ex:0 … ex:N-1). Специально
# «богатые» — показывают клиенту, что можно указывать характеристики: класс,
# объём, загрузку, уровень шума, функции, бюджет.
EXAMPLE_QUERIES: tuple[str, ...] = (
    "индукционная плита чёрная на 4 конфорки",
    "холодильник 350 л",
    "духовой шкаф с пиролизом",
)

# Подписи кнопок-примеров (в том же порядке, что EXAMPLE_QUERIES).
EXAMPLE_LABELS: tuple[str, ...] = (
    "🔥 Индукционная плита чёрная на 4 конфорки",
    "❄️ Холодильник 350 л",
    "🍳 Духовой шкаф с пиролизом",
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
        "• указывайте характеристики — цвет, размер, объём, класс, функции, бюджет\n\n"
        "<b>Примеры</b>\n"
        "• <i>духовой шкаф чёрный встраиваемый с пиролизом</i>\n"
        "• <i>посудомойка 60 см тихая до 45 дБ на 14 комплектов</i>\n"
        "• <i>холодильник No Frost от 350 литров до 90000</i>\n\n"
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
        "<b>Что можно указывать в запросе:</b>",
        "• размер и цвет: <i>60 см</i>, <i>чёрный</i>, <i>из нержавейки</i>",
        "• объём/загрузку: <i>от 350 литров</i>, <i>на 8 кг</i>, <i>14 комплектов</i>",
        "• класс и шум: <i>класс A+++</i>, <i>тихая до 45 дБ</i>",
        "• функции: <i>No Frost</i>, <i>пиролиз</i>, <i>гриль</i>, <i>сенсорное управление</i>",
        "• бюджет и исключения: <i>до 30000</i>, <i>не белую</i>, <i>кроме Indesit</i>",
        "",
        "Чем подробнее — тем точнее подбор.",
    ]
    return "\n".join(lines)


# Шоурумы и почта — со страницы контактов ekrom.ru/contacts/ (при смене данных
# на сайте поправить здесь).
_SHOP_EMAIL = "sale@ekrom.ru"
_SHOWROOMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Екатеринбург",
        (
            "ИЦ «Уют Холл» — ул. Толмачева, 22, 2 этаж · ежедневно 10:00–20:00",
            "ИЦ ASTROOM — ул. Цвиллинга, 1, корп. Б, 9 этаж · ежедневно 10:00–20:00",
            "МЦ ЭМА — Верх-Исетский бул., 13, 5 этаж · ежедневно 10:00–20:00",
        ),
    ),
    (
        "Тюмень",
        (
            "Шоурум — ул. Герцена, 55 · пн–пт 9:00–20:00, сб–вс 10:00–18:00",
        ),
    ),
)


def contacts_text(settings: Settings) -> str:
    shop = html.escape(settings.shop_name)
    site = html.escape(settings.shop_site.rstrip("/"))
    contacts = html.escape(settings.shop_contacts.strip()) if settings.shop_contacts else ""
    body = f"📞 <b>Контакты {shop}</b>\n\n"
    if contacts:
        body += f"{contacts}\n"
    body += f"✉️ {_SHOP_EMAIL}\n\n"
    body += "🏬 <b>Шоурумы — посмотреть технику вживую</b>\n"
    for city, rooms in _SHOWROOMS:
        body += f"\n<b>{html.escape(city)}</b>\n"
        for room in rooms:
            body += f"• {html.escape(room)}\n"
    body += f"\n🌐 Сайт: <a href=\"{site}\">{site}</a>\n\n"
    body += "<i>После подбора менеджер свяжется с вами при необходимости.</i>"
    return body


def examples_inline_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i, label in enumerate(EXAMPLE_LABELS):
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"ex:{i}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _phone_digits(contacts: str) -> str | None:
    m = re.search(r"(\+7[\d\s\-()]{10,})", contacts)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return digits if len(digits) >= 11 else None


def _whatsapp_url(contacts: str) -> str | None:
    # На странице контактов ekrom указан WhatsApp на основном номере.
    digits = _phone_digits(contacts)
    return f"https://wa.me/{digits}" if digits else None


def followup_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    # ВАЖНО: inline-кнопки Telegram принимают только http/https-URL — «tel:»
    # сервер отвергает целиком всё сообщение (Bad Request). Телефон кликабелен
    # прямо в тексте контактов, поэтому кнопки «Позвонить» нет.
    rows: list[list[InlineKeyboardButton]] = []
    site = settings.shop_site.rstrip("/")
    rows.append([InlineKeyboardButton(text="🌐 Сайт ekrom", url=site)])
    wa = _whatsapp_url(settings.shop_contacts)
    if wa:
        rows.append([InlineKeyboardButton(text="💬 Написать в WhatsApp", url=wa)])
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


# ── Режим «Собери комплект» (5 брендов по акциям) ────────────────────────────

def kit_welcome_text(settings: Settings) -> str:
    shop = html.escape(settings.shop_name)
    return (
        f"👋 <b>Добро пожаловать в {shop}!</b>\n\n"
        "Я собираю <b>комплект встраиваемой техники ОДНОГО бренда</b> и считаю "
        "выгоду по действующим акциям — со скидкой на комплект или техникой в подарок.\n\n"
        "<b>Как написать запрос</b>\n"
        "Перечислите нужные позиции одним сообщением, например:\n"
        "• <i>духовой шкаф, варочная панель и вытяжка</i>\n"
        "• <i>холодильник, посудомойка, духовка и варочная</i>\n\n"
        "Я подберу бренд, где комплект выгоднее всего, и пришлю <b>PDF</b>.\n\n"
        "<i>Совместимость по дизайну — визуальная (цвет/финиш); точность подтвердит менеджер.</i>"
    )


def format_kit(kit: object, settings: Settings) -> str:
    """Клиентское превью собранного комплекта: бренд, позиции, выгода, дизайн."""
    shop = html.escape(settings.shop_name)
    combo = kit.combo
    n = kit.coverage
    lines = [f"✅ <b>Комплект {html.escape(kit.brand)}</b> · {n} {_plural_pos(n)}"]
    if not kit.is_full:
        lines.append(
            f"<i>Закрыли {kit.coverage} из {kit.requested} — по остальному поможет менеджер.</i>"
        )
    lines.append("")

    for m in kit.selection.matched:
        p = m.product
        lines.append(f"• <b>{html.escape(p.title)}</b> — {_fmt_price(p.price or 0)}")
    for m in kit.selection.unmatched:
        pos = html.escape(m.spec.raw_text or "позиция")
        lines.append(f"• <i>{pos}: у бренда не нашлось подходящего</i>")

    lines.append("")
    lines.append(f"Сумма комплекта: <b>{_fmt_price(combo.subtotal)}</b>")
    if combo.applied and combo.gift_title:
        lines.append(
            f"🎁 В подарок: <b>{html.escape(combo.gift_title)}</b> "
            f"(стоимость {_fmt_price(combo.discount_total)}) — бесплатно"
        )
        lines.append(f"К оплате: <b>{_fmt_price(combo.final_total)}</b>")
    elif combo.applied:
        lines.append(f"Выгода по акции: <b>−{_fmt_price(combo.discount_total)}</b>")
        lines.append(f"К оплате: <b>{_fmt_price(combo.final_total)}</b>")
    elif combo.manager_review:
        lines.append("<i>По этой акции точную выгоду подтвердит менеджер.</i>")

    if kit.design_note:
        lines.append("")
        lines.append(f"🎨 {html.escape(kit.design_note)}")

    # Позиции вне охвата бота (мойки/смесители/чужие бренды) — подберёт менеджер.
    manager_specs = getattr(kit, "manager_specs", None) or []
    if manager_specs:
        lines.append("")
        lines.append("👤 <b>Эти позиции подберёт менеджер</b> (вне комплектов 5 брендов):")
        for s in manager_specs:
            lines.append(f"• {html.escape(s.raw_text or 'позиция')}")

    lines.append(
        f"\n<i>ℹ️ Автоподбор {shop}. Цены, наличие и итог по акции подтвердит менеджер.</i>"
    )
    return "\n".join(lines)


def kit_all_to_manager_text(kit: object) -> str:
    """Весь запрос вне охвата бота: честно передаём менеджеру (лид уже ушёл)."""
    lines = [
        "👤 <b>Эти позиции подберёт менеджер</b> — они вне комплектов "
        "5 брендов (Korting, Maunfeld, Midea, Lex, Kuppersberg):",
        "",
    ]
    for s in (getattr(kit, "manager_specs", None) or []):
        lines.append(f"• {html.escape(s.raw_text or 'позиция')}")
    lines.append("")
    lines.append("Я передал запрос — менеджер свяжется с вами. "
                 "А комплект техники из 5 брендов соберу сам: напишите позиции.")
    return "\n".join(lines)


def kit_pdf_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📄 Скачать PDF комплекта", callback_data=f"kitpdf:{token}"),
    ]])


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
