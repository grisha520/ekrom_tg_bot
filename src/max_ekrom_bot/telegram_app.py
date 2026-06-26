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
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
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

from .config import Settings, get_settings
from .ekrom.client import EkromAuthError, EkromCartError
from .managers import ManagerStore
from .messenger.telegram_format import format_selection
from .models import SelectionResult
from .pipeline import Pipeline

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


def _user_allowed(user_id: int | None) -> bool:
    if user_id is None:
        return False
    return _managers().contains(user_id) or _is_admin(user_id)


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


# Кнопки постоянной клавиатуры (нажатие шлёт этот текст — ловим его в handle_text).
BTN_STATUS = "🔁 Проверить вход"
BTN_LOGIN = "🔐 Обновить вход"
BTN_HELP = "ℹ️ Как пользоваться"
BTN_MANAGERS = "👥 Менеджеры"


def _main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Постоянная клавиатура под полем ввода. Админу добавляется «👥 Менеджеры»."""
    rows = [
        [KeyboardButton(text=BTN_STATUS), KeyboardButton(text=BTN_LOGIN)],
        [KeyboardButton(text=BTN_HELP)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=BTN_MANAGERS)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Вставьте сообщение клиента…",
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
    if not _user_allowed(uid):
        await _deny(message)
        return
    admin = _is_admin(uid)
    await message.answer(
        _help_text(admin), parse_mode=ParseMode.HTML, reply_markup=_main_keyboard(admin)
    )


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
    if not _user_allowed(uid):
        await _deny(message)
        return

    # Нажатия кнопок постоянной клавиатуры приходят как обычный текст — разводим
    # их по действиям, не отдавая в подбор.
    text = message.text.strip()
    if text == BTN_STATUS:
        await cmd_status(message)
        return
    if text == BTN_LOGIN:
        await cmd_login(message)
        return
    if text == BTN_HELP:
        await cmd_start(message)
        return
    admin = _is_admin(uid)
    if admin and text == BTN_MANAGERS:
        await _show_managers(message)
        return
    if admin and text.isdigit():
        await _ask_add(message, int(text))
        return

    chat_id = message.chat.id
    lock = _chat_lock(chat_id)
    if lock.locked():
        await message.answer("⏳ Уже обрабатываю предыдущий запрос — подождите.")
        return

    async with lock:
        # Без reply-клавиатуры: это сообщение затем редактируется (edit_text)
        # и на него навешивается inline-кнопка PDF. Telegram не позволяет
        # editMessageText для сообщения, отправленного с ReplyKeyboardMarkup
        # («message can't be edited»). Постоянная клавиатура и так висит с /start.
        status = await message.answer(
            "⏳ Разбираю запрос и подбираю товары на ekrom…\n"
            "<i>Обычно 30 секунд.</i>",
            parse_mode=ParseMode.HTML,
        )
        pipeline = Pipeline(get_settings())

        try:
            request = pipeline.parse_message(message.text.strip())
        except (ValueError, ValidationError):
            await status.edit_text(
                "❌ Не удалось разобрать сообщение.\n"
                "Попробуйте оформить позиции нумерованным списком."
            )
            return
        except Exception:  # noqa: BLE001
            await status.edit_text(
                "❌ Сервис разбора недоступен (Groq / сеть).\n"
                "Проверьте GROQ_API_KEY в .env."
            )
            return

        try:
            async with _EKROM_LOCK:
                selection = await pipeline.select_products(request)
        except Exception:  # noqa: BLE001
            logger.exception("select_products failed")
            await status.edit_text("❌ Ошибка при подборе на ekrom. Попробуйте позже.")
            return

        preview = format_selection(selection)
        if len(preview) > 4000:
            preview = preview[:3990] + "…"

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
    dp.message.register(cmd_status, Command("status"))
    dp.message.register(cmd_login, Command("login"))
    dp.message.register(handle_text, F.text & ~F.text.startswith("/"))
    dp.callback_query.register(callback_pdf, F.data.startswith("pdf:"))
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
    if not settings.allowed_telegram_user_ids:
        raise SystemExit("TELEGRAM_ALLOWED_IDS не задан — укажите user_id менеджеров")

    bot = _make_bot(settings)
    await _wait_telegram(bot)
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="ℹ️ Как пользоваться"),
            BotCommand(command="status", description="🔁 Проверить вход в ekrom"),
            BotCommand(command="login", description="🔐 Обновить вход в ekrom"),
        ])
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
