"""Telegram-бот для менеджеров: подбор техники ekrom + PDF.

Запуск на локальном ПК (long polling — отдельный сервер не нужен,
но процесс бота должен быть запущен, пока менеджеры пользуются им):

  python -m max_ekrom_bot.telegram_app

Или: scripts/start_telegram_bot.ps1
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import ValidationError

from .bot_metrics import BotMetricsStore, format_admin_stats
from .config import Settings, get_settings
from .ekrom.client import EkromAuthError, EkromCartError
from .managers import ManagerStore
from .messenger.client_ux import (
    BTN_CONTACTS,
    BTN_EXAMPLES,
    BTN_HELP,
    BTN_HISTORY,
    EXAMPLE_QUERIES,
    client_status_parse,
    client_status_pdf,
    client_status_select,
    contacts_text,
    empty_result_text,
    examples_inline_keyboard,
    examples_text,
    followup_keyboard,
    followup_text,
    format_client_selection,
    format_history_list,
    history_inline_keyboard,
    is_smalltalk,
    pdf_caption,
    smalltalk_reply,
    welcome_text,
)
from .messenger.telegram_format import format_selection
from .models import SelectionResult
from .pdf import build_offer_pdf
from .pick_history import PickHistoryStore
from .pipeline import Pipeline
from .ratelimit import RateLimiter

logger = logging.getLogger(__name__)


@dataclass
class _Pending:
    selection: SelectionResult


# Подборы между шагом «превью» и нажатием «Сформировать PDF».
_STORE: dict[str, _Pending] = {}
# Не даём одному чату запускать два тяжёлых процесса одновременно.
_BUSY: dict[int, asyncio.Lock] = {}
# Playwright/ekrom — один браузерный процесс за раз (иначе /login и подбор конфликтуют).
_EKROM_LOCK = asyncio.Lock()


def _ensure_playwright_env() -> None:
    """Путь к Chromium для Playwright (важно при запуске не через .ps1)."""
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        default = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
        if default.is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(default)


def _format_ekrom_error(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if "executable doesn't exist" in low or "browserType.launch" in msg:
        return (
            "❌ Chromium для Playwright не найден.\n\n"
            "На ПК с ботом выполните:\n"
            "  playwright install chromium\n"
            "И перезапустите бота."
        )
    if "timeout" in low:
        return (
            "❌ Сайт ekrom не ответил вовремя.\n"
            "Проверьте интернет и повторите /login."
        )
    logger.exception("ekrom error")
    short = msg[:200] + ("…" if len(msg) > 200 else "")
    return f"❌ Ошибка ekrom/браузера:\n<code>{short}</code>"


_MANAGERS: ManagerStore | None = None


def _managers() -> ManagerStore:
    """Хранилище менеджеров (правится из бота). Сидится из .env при первом запуске."""
    global _MANAGERS
    if _MANAGERS is None:
        s = get_settings()
        _MANAGERS = ManagerStore(s.managers_store_path)
        _MANAGERS.seed(s.allowed_telegram_user_ids)
    return _MANAGERS


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in get_settings().admin_user_ids


def _is_operator(user_id: int | None) -> bool:
    """Оператор/менеджер — есть в allowlist или админ. Доступ к служебным функциям
    (проверка/обновление входа ekrom, управление менеджерами)."""
    if user_id is None:
        return False
    return _managers().contains(user_id) or _is_admin(user_id)


# Совместимость со старым именем (доступ оператора).
_user_allowed = _is_operator


def _can_use(user_id: int | None) -> bool:
    """Может ли пользователь делать подбор. В клиентском режиме — все; иначе —
    только операторы (прежнее поведение «бот для менеджеров»)."""
    return get_settings().client_mode or _is_operator(user_id)


_RATELIMIT: RateLimiter | None = None
_HISTORY: PickHistoryStore | None = None
_METRICS: BotMetricsStore | None = None


def _ratelimiter() -> RateLimiter:
    global _RATELIMIT
    if _RATELIMIT is None:
        s = get_settings()
        _RATELIMIT = RateLimiter(s.client_rate_per_hour, s.client_rate_per_day)
    return _RATELIMIT


def _history() -> PickHistoryStore:
    global _HISTORY
    if _HISTORY is None:
        s = get_settings()
        _HISTORY = PickHistoryStore(s.analytics_db_path, s.pick_history_dir)
    return _HISTORY


def _metrics() -> BotMetricsStore:
    global _METRICS
    if _METRICS is None:
        _METRICS = BotMetricsStore(get_settings().analytics_db_path)
    return _METRICS


def _record_pick_metric(
    user_id: int | None,
    *,
    client: bool,
    success: bool,
    matched_count: int,
    duration_sec: float,
    queue_wait_sec: float,
) -> None:
    if user_id is None:
        return
    try:
        _metrics().record(
            user_id=user_id,
            is_client=client,
            success=success,
            matched_count=matched_count,
            duration_sec=duration_sec,
            queue_wait_sec=queue_wait_sec,
        )
    except Exception:  # noqa: BLE001 — метрики не критичны
        logger.warning("не удалось записать метрику", exc_info=True)


# Запросы доступа, о которых уже уведомили админов (чтобы не спамить), и подписи
# к ним для кнопки «Добавить».
_ACCESS_REQUESTED: set[int] = set()
_REQUEST_LABELS: dict[int, str] = {}


def _user_label(user) -> str:
    uname = f"@{user.username}" if user.username else "без username"
    return f"{user.full_name} ({uname}, id={user.id})"


class AccessLogMiddleware(BaseMiddleware):
    """Логирует каждое входящее обращение (user_id, username, имя) и решение о доступе.

    Нужно, чтобы добавлять новых менеджеров без @userinfobot: человек просто
    пишет боту, а его user_id видно в логах (journalctl -u max-ekrom-bot).
    Отклонённые пишутся уровнем WARNING с готовой подсказкой, кого добавить.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is not None:
            uname = f"@{user.username}" if user.username else "без username"
            if _user_allowed(user.id):
                logger.info(
                    "Обращение: user_id=%s %s (%s) — доступ разрешён",
                    user.id, uname, user.full_name,
                )
            else:
                logger.warning(
                    "Обращение БЕЗ доступа: user_id=%s %s (%s). Чтобы выдать доступ — "
                    "добавьте %s в TELEGRAM_ALLOWED_IDS и перезапустите бота",
                    user.id, uname, user.full_name, user.id,
                )
        return await handler(event, data)


def _chat_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in _BUSY:
        _BUSY[chat_id] = asyncio.Lock()
    return _BUSY[chat_id]


def _deny_message() -> str:
    return (
        "⛔ Доступ запрещён.\n\n"
        "Этот бот только для менеджеров ekrom. Запрос на доступ отправлен "
        "администратору — он подтвердит его в один тап."
    )


async def _deny(message: Message) -> None:
    """Отказать в доступе и уведомить админов с кнопкой «✅ Добавить» (один раз)."""
    await message.answer(_deny_message())
    user = message.from_user
    if user is None or user.id in _ACCESS_REQUESTED:
        return
    _ACCESS_REQUESTED.add(user.id)
    _REQUEST_LABELS[user.id] = _user_label(user)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Добавить", callback_data=f"addmgr:{user.id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"denymgr:{user.id}"),
    ]])
    for admin_id in get_settings().admin_user_ids:
        try:
            await message.bot.send_message(
                admin_id, f"🔔 Запрос доступа к боту:\n{_REQUEST_LABELS[user.id]}",
                reply_markup=kb,
            )
        except Exception:  # noqa: BLE001 — не удалось уведомить одного админа
            logger.warning("не удалось уведомить админа %s", admin_id, exc_info=True)


async def _notify_lead(message: Message, selection: SelectionResult) -> None:
    """Уведомить операторов о новом запросе клиента (лид)."""
    s = get_settings()
    if not s.notify_lead or not s.admin_user_ids:
        return
    user = message.from_user
    who = _user_label(user) if user else "аноним"
    text = (message.text or "").strip()
    if len(text) > 300:
        text = text[:300] + "…"
    if selection.matched:
        result = f"подобрано {len(selection.matched)} поз., итог {int(selection.total_price)} ₽"
    else:
        result = "ничего не подобрано (спрос без результата)"
    note = f"🧲 <b>Запрос клиента</b>\n{who}\n\n«{text}»\n\n{result}"
    for admin_id in s.admin_user_ids:
        try:
            await message.bot.send_message(admin_id, note, parse_mode=ParseMode.HTML)
        except Exception:  # noqa: BLE001 — не удалось уведомить одного оператора
            logger.warning("не удалось отправить лид админу %s", admin_id, exc_info=True)


def _rate_limit_text() -> str:
    return (
        "⏳ Вы отправили много запросов за короткое время.\n"
        "Пожалуйста, попробуйте чуть позже — или напишите менеджеру, он поможет."
    )


# Кнопки постоянной клавиатуры оператора (нажатие шлёт этот текст).
BTN_STATUS = "🔁 Проверить вход"
BTN_LOGIN = "🔐 Обновить вход"
BTN_MANAGERS = "👥 Менеджеры"
BTN_STATS = "📊 Статистика"


def _main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Постоянная клавиатура под полем ввода. Админу добавляется «👥 Менеджеры»."""
    rows = [
        [KeyboardButton(text=BTN_STATUS), KeyboardButton(text=BTN_LOGIN)],
        [KeyboardButton(text=BTN_HELP)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=BTN_MANAGERS), KeyboardButton(text=BTN_STATS)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Вставьте сообщение клиента…",
    )


def _client_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура клиента: примеры, справка, контакты."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_EXAMPLES), KeyboardButton(text=BTN_HELP)],
            [KeyboardButton(text=BTN_HISTORY), KeyboardButton(text=BTN_CONTACTS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Опишите технику — цвет, размер, бюджет…",
    )


def _help_text(is_admin: bool = False) -> str:
    text = (
        "<b>Подбор техники ekrom</b>\n\n"
        "📋 Просто пришлите сообщение клиента (списком позиций) — бот подберёт "
        "товары и покажет превью с ценами. Затем нажмите кнопку "
        "«📄 Сформировать PDF».\n\n"
        "Кнопки под полем ввода:\n"
        f"{BTN_STATUS} — проверить сессию ekrom\n"
        f"{BTN_LOGIN} — обновить вход в ekrom\n"
        f"{BTN_HELP} — эта справка\n"
    )
    if is_admin:
        text += (
            f"{BTN_MANAGERS} — список менеджеров, добавить/удалить\n"
            f"{BTN_STATS} — нагрузка и время подбора (сегодня)\n"
            "\n<i>Когда новый человек напишет боту — вам придёт кнопка «Добавить».</i>\n"
        )
    return text + "\nПодбор занимает ~30 с, PDF ещё ~30 с."


def _managers_view() -> tuple[str, InlineKeyboardMarkup | None]:
    """Текст списка менеджеров + инлайн-кнопки удаления для каждого."""
    items = _managers().items()
    lines = [f"👥 <b>Менеджеров: {len(items)}</b>", ""]
    rows: list[list[InlineKeyboardButton]] = []
    for uid, label in sorted(items.items()):
        lines.append(f"• {label}")
        rows.append([InlineKeyboardButton(
            text=f"➖ Удалить {label[:28]}", callback_data=f"delmgr:{uid}"
        )])
    lines += [
        "",
        "➕ <i>Добавить:</i> пусть человек напишет боту (вам придёт кнопка), "
        "или пришлите его числовой user_id.",
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    return "\n".join(lines), kb


async def _show_managers(message: Message) -> None:
    text, kb = _managers_view()
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _ask_add(message: Message, uid: int) -> None:
    """Спросить подтверждение добавления менеджера по присланному user_id."""
    if _managers().contains(uid):
        await message.answer(f"✅ id={uid} уже в списке менеджеров.")
        return
    _REQUEST_LABELS.setdefault(uid, str(uid))
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Добавить", callback_data=f"addmgr:{uid}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"denymgr:{uid}"),
    ]])
    await message.answer(f"Добавить менеджера id={uid}?", reply_markup=kb)


def _pdf_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Сформировать PDF", callback_data=f"pdf:{token}")]
        ]
    )


async def cmd_start(message: Message) -> None:
    uid = message.from_user.id if message.from_user else None
    if _is_operator(uid):
        admin = _is_admin(uid)
        await message.answer(
            _help_text(admin), parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(admin),
        )
        return
    if get_settings().client_mode:
        s = get_settings()
        await message.answer(
            welcome_text(s),
            parse_mode=ParseMode.HTML,
            reply_markup=_client_keyboard(),
            disable_web_page_preview=True,
        )
        await message.answer(
            "👇 Быстрый старт — нажмите пример:",
            reply_markup=examples_inline_keyboard(),
        )
        return
    await _deny(message)


async def cmd_examples(message: Message) -> None:
    if not get_settings().client_mode or _is_operator(message.from_user.id if message.from_user else None):
        await cmd_start(message)
        return
    await message.answer(
        examples_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=examples_inline_keyboard(),
    )


async def cmd_contacts(message: Message) -> None:
    if not get_settings().client_mode or _is_operator(message.from_user.id if message.from_user else None):
        await cmd_start(message)
        return
    s = get_settings()
    await message.answer(
        contacts_text(s),
        parse_mode=ParseMode.HTML,
        reply_markup=followup_keyboard(s),
        disable_web_page_preview=True,
    )


async def _show_client_history(message: Message) -> None:
    uid = message.from_user.id if message.from_user else None
    if uid is None:
        return
    records = _history().list_for_user(uid, limit=10)
    await message.answer(
        format_history_list(records),
        parse_mode=ParseMode.HTML,
        reply_markup=history_inline_keyboard(records) if records else None,
    )


async def cmd_history(message: Message) -> None:
    if not get_settings().client_mode or _is_operator(message.from_user.id if message.from_user else None):
        await cmd_start(message)
        return
    await _show_client_history(message)


async def cmd_stats(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer("Только для администратора.")
        return
    try:
        stats = _metrics().stats()
        await message.answer(format_admin_stats(stats), parse_mode=ParseMode.HTML)
    except Exception:  # noqa: BLE001
        logger.exception("stats failed")
        await message.answer("Не удалось собрать статистику.")


async def cmd_status(message: Message) -> None:
    if not _user_allowed(message.from_user.id if message.from_user else None):
        await _deny(message)
        return
    status = await message.answer("⏳ Проверяю сессию ekrom…")
    pipeline = Pipeline(get_settings())
    try:
        async with _EKROM_LOCK:
            ok = await pipeline.is_ekrom_authenticated()
    except Exception as exc:  # noqa: BLE001
        await status.edit_text(_format_ekrom_error(exc), parse_mode=ParseMode.HTML)
        return
    if ok:
        await status.edit_text("🟢 Сессия ekrom активна — вход выполнен.")
    else:
        await status.edit_text(
            "⚪ Вход в ekrom не выполнен или сессия истекла.\n"
            f"Нажмите кнопку «{BTN_LOGIN}» или на ПК с ботом:\n"
            "<code>python scripts/login_ekrom.py</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_login(message: Message) -> None:
    if not _user_allowed(message.from_user.id if message.from_user else None):
        await _deny(message)
        return
    status = await message.answer(
        "⏳ Проверяю вход в ekrom…\n<i>Может занять до 30 с.</i>",
        parse_mode=ParseMode.HTML,
    )
    pipeline = Pipeline(get_settings())
    try:
        async with _EKROM_LOCK:
            already = await pipeline.refresh_ekrom_session()
    except ValueError:
        await status.edit_text("❌ В .env не заданы EKROM_LOGIN и EKROM_PASSWORD.")
        return
    except EkromAuthError as exc:
        await status.edit_text(f"❌ Не удалось войти: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        await status.edit_text(_format_ekrom_error(exc), parse_mode=ParseMode.HTML)
        return

    if already:
        await status.edit_text("🟢 Вход уже был активен — повторный вход не нужен.")
    else:
        await status.edit_text("🟢 Сессия ekrom обновлена. Можно подбирать и формировать PDF.")


async def handle_text(message: Message) -> None:
    if not message.text or not message.chat:
        return
    uid = message.from_user.id if message.from_user else None
    if not _can_use(uid):
        await _deny(message)
        return

    text = message.text.strip()
    op = _is_operator(uid)
    client = get_settings().client_mode and not op

    if text == BTN_HELP:
        await cmd_start(message)
        return
    if client and text == BTN_EXAMPLES:
        await cmd_examples(message)
        return
    if client and text == BTN_CONTACTS:
        await cmd_contacts(message)
        return
    if client and text == BTN_HISTORY:
        await cmd_history(message)
        return

    if op:
        if text == BTN_STATUS:
            await cmd_status(message)
            return
        if text == BTN_LOGIN:
            await cmd_login(message)
            return
        if _is_admin(uid) and text == BTN_MANAGERS:
            await _show_managers(message)
            return
        if _is_admin(uid) and text == BTN_STATS:
            await cmd_stats(message)
            return
        if _is_admin(uid) and text.isdigit():
            await _ask_add(message, int(text))
            return

    if client and is_smalltalk(text):
        await message.answer(
            smalltalk_reply(get_settings().shop_name),
            parse_mode=ParseMode.HTML,
        )
        return

    if client and uid is not None:
        rl = _ratelimiter()
        if not rl.allowed(uid):
            await message.answer(_rate_limit_text())
            return
        rl.hit(uid)

    await _run_pick(message, text, op=op, client=client)


async def _run_pick(message: Message, text: str, *, op: bool, client: bool) -> None:
    """Разбор запроса → подбор → PDF (клиент) или превью+кнопка (оператор)."""
    uid = message.from_user.id if message.from_user else None
    t0 = time.perf_counter()
    queue_wait = 0.0
    success = False
    matched_count = 0

    chat_id = message.chat.id
    lock = _chat_lock(chat_id)
    if lock.locked():
        await message.answer("⏳ Уже обрабатываю предыдущий запрос — подождите.")
        return

    async with lock:
        try:
            await message.bot.send_chat_action(chat_id, ChatAction.TYPING)
            status_text = (
                client_status_parse() if client else
                "⏳ Разбираю запрос и подбираю товары на ekrom…\n<i>Обычно 30 секунд.</i>"
            )
            status = await message.answer(status_text, parse_mode=ParseMode.HTML)
            pipeline = Pipeline(get_settings())

            try:
                request = pipeline.parse_message(text)
            except (ValueError, ValidationError):
                err = (
                    "🤔 Не понял запрос. Напишите, какую технику ищете — или нажмите 💡 Примеры."
                    if client else
                    "❌ Не удалось разобрать сообщение.\n"
                    "Попробуйте оформить позиции нумерованным списком."
                )
                await status.edit_text(err)
                return
            except Exception:  # noqa: BLE001
                err = (
                    "⚙️ Сервис временно недоступен. Попробуйте, пожалуйста, чуть позже."
                    if client else
                    "❌ Сервис разбора недоступен (Groq / сеть).\n"
                    "Проверьте GROQ_API_KEY в .env."
                )
                await status.edit_text(err)
                return

            if client:
                await message.bot.send_chat_action(chat_id, ChatAction.TYPING)
                await status.edit_text(client_status_select(), parse_mode=ParseMode.HTML)

            try:
                queue_t0 = time.perf_counter()
                async with _EKROM_LOCK:
                    queue_wait = time.perf_counter() - queue_t0
                    selection = await pipeline.select_products(
                        request, enrich_chosen=client
                    )
            except Exception:  # noqa: BLE001
                logger.exception("select_products failed")
                err = (
                    "😔 Не удалось подобрать сейчас. Попробуйте через минуту или напишите менеджеру."
                    if client else
                    "❌ Ошибка при подборе на ekrom. Попробуйте позже."
                )
                await status.edit_text(err)
                return

            success = True
            matched_count = len(selection.matched)

            preview = (
                format_client_selection(selection, get_settings().shop_name)
                if client else format_selection(selection)
            )
            if len(preview) > 4000:
                preview = preview[:3990] + "…"

            if client:
                if selection.matched:
                    await status.edit_text(preview, parse_mode=ParseMode.HTML)
                    await message.bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
                    pdf_status = await message.answer(
                        client_status_pdf(), parse_mode=ParseMode.HTML
                    )
                    try:
                        pdf_path = await asyncio.to_thread(
                            build_offer_pdf, selection, get_settings()
                        )
                        await message.answer_document(
                            FSInputFile(pdf_path), caption=pdf_caption()
                        )
                        await pdf_status.delete()
                        if uid is not None:
                            _history().add(
                                uid,
                                text,
                                pdf_path,
                                total_price=selection.total_price,
                                matched_count=matched_count,
                            )
                    except Exception:  # noqa: BLE001
                        logger.exception("client offer pdf failed")
                        await pdf_status.edit_text(
                            "Подбор выше 👆 PDF не удалось собрать — менеджер пришлёт отдельно."
                        )
                    s = get_settings()
                    await message.answer(
                        followup_text(s),
                        parse_mode=ParseMode.HTML,
                        reply_markup=followup_keyboard(s),
                        disable_web_page_preview=True,
                    )
                else:
                    await status.edit_text(empty_result_text(), parse_mode=ParseMode.HTML)
                await _notify_lead(message, selection)
                return

            token = uuid.uuid4().hex
            _STORE[token] = _Pending(selection=selection)

            if selection.matched:
                await status.edit_text(
                    preview,
                    parse_mode=ParseMode.HTML,
                    reply_markup=_pdf_keyboard(token),
                )
            else:
                await status.edit_text(preview, parse_mode=ParseMode.HTML)
        finally:
            _record_pick_metric(
                uid,
                client=client,
                success=success,
                matched_count=matched_count,
                duration_sec=time.perf_counter() - t0,
                queue_wait_sec=queue_wait,
            )


async def callback_pdf(callback: CallbackQuery) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    if not _user_allowed(callback.from_user and callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    token = callback.data.removeprefix("pdf:")
    pending = _STORE.get(token)
    if not pending:
        await callback.answer("Подбор устарел — отправьте запрос заново.", show_alert=True)
        return

    chat_id = callback.message.chat.id
    lock = _chat_lock(chat_id)
    if lock.locked():
        await callback.answer("Уже формирую PDF — подождите.", show_alert=True)
        return

    await callback.answer("Формирую PDF…")

    async with lock:
        status = await callback.message.answer(
            "⏳ Добавляю товары в корзину и формирую PDF…\n"
            "<i>Обычно 30 секунд.</i>",
            parse_mode=ParseMode.HTML,
        )
        pipeline = Pipeline(get_settings())
        try:
            async with _EKROM_LOCK:
                path = await pipeline.build_pdf(pending.selection)
        except EkromCartError as exc:
            items = "; ".join(exc.missing_titles)
            await status.edit_text(
                f"❌ Не удалось добавить в корзину:\n{items}\n\n"
                "Повторите попытку или уберите эти позиции."
            )
            return
        except EkromAuthError:
            await status.edit_text(
                "❌ Сессия ekrom истекла.\n"
                "Нажмите /login или обновите сессию на ПК, где запущен бот."
            )
            return
        except PlaywrightTimeoutError:
            await status.edit_text(
                "❌ Сайт ekrom не ответил вовремя.\n"
                "Проверьте /status и повторите."
            )
            return
        except Exception:  # noqa: BLE001
            logger.exception("build_pdf failed")
            await status.edit_text("❌ Не удалось сформировать PDF. Попробуйте ещё раз.")
            return
        finally:
            _STORE.pop(token, None)

        await status.edit_text("✅ PDF готов — отправляю файл…")
        await callback.message.answer_document(
            FSInputFile(path),
            caption=f"Подбор ekrom ({path.name})",
        )


async def callback_example(callback: CallbackQuery) -> None:
    """Клиент нажал готовый пример — запускаем подбор."""
    if not callback.data or not callback.message:
        await callback.answer()
        return
    uid = callback.from_user.id if callback.from_user else None
    if not get_settings().client_mode or _is_operator(uid):
        await callback.answer()
        return

    if callback.data == "ex:menu":
        await callback.answer()
        await callback.message.answer(
            examples_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=examples_inline_keyboard(),
        )
        return

    try:
        idx = int(callback.data.removeprefix("ex:"))
        query = EXAMPLE_QUERIES[idx]
    except (ValueError, IndexError):
        await callback.answer()
        return

    await callback.answer("Подбираю…")
    op = _is_operator(uid)
    client = get_settings().client_mode and not op
    if client and uid is not None:
        rl = _ratelimiter()
        if not rl.allowed(uid):
            await callback.message.answer(_rate_limit_text())
            return
        rl.hit(uid)
    await _run_pick(callback.message, query, op=op, client=client)


async def callback_history(callback: CallbackQuery) -> None:
    """Повторная отправка PDF из истории клиента."""
    if not callback.data or not callback.message:
        await callback.answer()
        return
    uid = callback.from_user.id if callback.from_user else None
    if uid is None or not get_settings().client_mode or _is_operator(uid):
        await callback.answer("Недоступно", show_alert=True)
        return

    if callback.data == "hist:menu":
        await callback.answer()
        await _show_client_history(callback.message)
        return

    try:
        record_id = int(callback.data.removeprefix("hist:"))
    except ValueError:
        await callback.answer()
        return

    rec = _history().get(record_id, uid)
    if rec is None or not rec.pdf_path.is_file():
        await callback.answer("Подбор не найден или PDF удалён", show_alert=True)
        return

    await callback.answer("Отправляю PDF…")
    await callback.message.answer_document(
        FSInputFile(rec.pdf_path),
        caption=f"📄 {rec.query_text[:120]}",
    )


async def callback_manage(callback: CallbackQuery) -> None:
    """Админские действия по инлайн-кнопкам: добавить/удалить/отклонить менеджера."""
    if not _is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Только для администратора", show_alert=True)
        return
    try:
        action, raw_id = (callback.data or "").split(":", 1)
        uid = int(raw_id)
    except ValueError:
        await callback.answer()
        return

    if action == "addmgr":
        label = _REQUEST_LABELS.get(uid, str(uid))
        added = _managers().add(uid, label)
        _ACCESS_REQUESTED.discard(uid)
        await callback.answer("Добавлен" if added else "Уже в списке")
        try:
            await callback.message.edit_text(
                f"✅ Менеджер добавлен: {label}" if added else f"✅ {label} уже был в списке."
            )
        except Exception:  # noqa: BLE001
            pass
        if added:
            try:
                await callback.bot.send_message(
                    uid, "✅ Вам выдан доступ к боту. Нажмите /start, чтобы начать."
                )
            except Exception:  # noqa: BLE001 — пользователь мог не открывать чат с ботом
                pass
    elif action == "delmgr":
        removed = _managers().remove(uid)
        await callback.answer("Удалён" if removed else "Не найден")
        text, kb = _managers_view()
        try:
            await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:  # noqa: BLE001
            pass
    elif action == "denymgr":
        _ACCESS_REQUESTED.discard(uid)
        await callback.answer("Отклонено")
        try:
            await callback.message.edit_text("❌ Запрос на доступ отклонён.")
        except Exception:  # noqa: BLE001
            pass
    else:
        await callback.answer()


def _make_bot(settings: Settings) -> Bot:
    """Создать Bot; при необходимости — через прокси (VPN/локальный SOCKS)."""
    proxy = (settings.telegram_proxy or "").strip()
    if proxy:
        logger.info("Telegram API через прокси: %s", proxy.split("@")[-1])
        session = AiohttpSession(proxy=proxy)
        return Bot(token=settings.telegram_bot_token, session=session)
    return Bot(token=settings.telegram_bot_token)


async def _wait_telegram(bot: Bot, attempts: int = 6) -> None:
    """Дождаться доступности api.telegram.org (с повторами)."""
    for n in range(attempts):
        try:
            me = await bot.get_me()
            logger.info("Бот @%s подключён к Telegram API", me.username)
            return
        except TelegramNetworkError as exc:
            if n >= attempts - 1:
                raise SystemExit(
                    "Не удалось подключиться к api.telegram.org.\n\n"
                    "Частая причина в РФ — блокировка или нестабильный доступ.\n"
                    "Что сделать:\n"
                    "  1) Включите VPN на этом ПК\n"
                    "  2) Или укажите в .env прокси, например:\n"
                    "     TELEGRAM_PROXY=socks5://127.0.0.1:10808\n\n"
                    f"Техническая ошибка: {exc}"
                ) from exc
            delay = 5 * (n + 1)
            logger.warning(
                "Telegram API недоступен (%s), повтор через %s с…",
                exc.__class__.__name__,
                delay,
            )
            await asyncio.sleep(delay)


def _build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    # Логируем все входящие обращения (включая отклонённые) — чтобы выдавать
    # доступ новым менеджерам по user_id из логов, без @userinfobot.
    access_log = AccessLogMiddleware()
    dp.message.outer_middleware.register(access_log)
    dp.callback_query.outer_middleware.register(access_log)
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_examples, Command("examples"))
    dp.message.register(cmd_contacts, Command("contacts"))
    dp.message.register(cmd_history, Command("history"))
    dp.message.register(cmd_stats, Command("stats"))
    dp.message.register(cmd_status, Command("status"))
    dp.message.register(cmd_login, Command("login"))
    dp.message.register(handle_text, F.text & ~F.text.startswith("/"))
    dp.callback_query.register(callback_pdf, F.data.startswith("pdf:"))
    dp.callback_query.register(callback_example, F.data.startswith("ex:"))
    dp.callback_query.register(callback_history, F.data.startswith("hist:"))
    dp.callback_query.register(
        callback_manage,
        lambda q: bool(q.data) and q.data.split(":", 1)[0] in {"addmgr", "delmgr", "denymgr"},
    )
    return dp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _ensure_playwright_env()
    pw_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "(default)")
    logger.info("PLAYWRIGHT_BROWSERS_PATH=%s", pw_path)

    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан — заполните .env")
    if not settings.client_mode and not settings.allowed_telegram_user_ids:
        raise SystemExit("TELEGRAM_ALLOWED_IDS не задан — укажите user_id менеджеров")

    bot = _make_bot(settings)
    await _wait_telegram(bot)
    try:
        if settings.client_mode:
            commands = [
                BotCommand(command="start", description="🏠 Начать"),
                BotCommand(command="history", description="📂 Мои подборы"),
                BotCommand(command="examples", description="💡 Примеры запросов"),
                BotCommand(command="contacts", description="📞 Контакты магазина"),
            ]
        else:
            commands = [
                BotCommand(command="start", description="ℹ️ Как пользоваться"),
                BotCommand(command="status", description="🔁 Проверить вход в ekrom"),
                BotCommand(command="login", description="🔐 Обновить вход в ekrom"),
            ]
        if settings.admin_user_ids:
            commands = list(commands) + [
                BotCommand(command="stats", description="📊 Статистика (админ)"),
            ]
        await bot.set_my_commands(commands)
    except Exception:  # noqa: BLE001 — меню команд не критично
        logger.warning("не удалось установить меню команд", exc_info=True)
    dp = _build_dispatcher()
    logger.info(
        "Telegram-бот запущен (long polling). Менеджеров: %s",
        len(settings.allowed_telegram_user_ids),
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
