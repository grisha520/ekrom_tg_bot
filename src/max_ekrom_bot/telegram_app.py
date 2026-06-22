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
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import ValidationError

from .config import Settings, get_settings
from .ekrom.client import EkromAuthError, EkromCartError
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


def _user_allowed(user_id: int | None) -> bool:
    if user_id is None:
        return False
    allowed = get_settings().allowed_telegram_user_ids
    return user_id in allowed


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
        "Этот бот только для менеджеров ekrom. "
        "Если вам нужен доступ — попросите администратора добавить ваш Telegram user_id "
        "в TELEGRAM_ALLOWED_IDS."
    )


def _help_text() -> str:
    return (
        "<b>Подбор техники ekrom</b>\n\n"
        "Отправьте сообщение клиента (списком позиций) — бот подберёт товары на сайте.\n\n"
        "<b>Команды:</b>\n"
        "/start — эта справка\n"
        "/status — проверить сессию ekrom\n"
        "/login — обновить вход в ekrom\n\n"
        "После подбора нажмите кнопку «Сформировать PDF».\n"
        "Подбор занимает ~30–90 с, PDF ещё ~30–60 с."
    )


def _pdf_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Сформировать PDF", callback_data=f"pdf:{token}")]
        ]
    )


async def cmd_start(message: Message) -> None:
    if not _user_allowed(message.from_user and message.from_user.id):
        await message.answer(_deny_message())
        return
    await message.answer(_help_text(), parse_mode=ParseMode.HTML)


async def cmd_status(message: Message) -> None:
    if not _user_allowed(message.from_user and message.from_user.id):
        await message.answer(_deny_message())
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
            "Нажмите /login или на ПК с ботом:\n"
            "<code>python scripts/login_ekrom.py</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_login(message: Message) -> None:
    if not _user_allowed(message.from_user and message.from_user.id):
        await message.answer(_deny_message())
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
    if not _user_allowed(message.from_user and message.from_user.id):
        await message.answer(_deny_message())
        return

    chat_id = message.chat.id
    lock = _chat_lock(chat_id)
    if lock.locked():
        await message.answer("⏳ Уже обрабатываю предыдущий запрос — подождите.")
        return

    async with lock:
        status = await message.answer(
            "⏳ Разбираю запрос и подбираю товары на ekrom…\n"
            "<i>Обычно 30–90 секунд.</i>",
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
            "<i>Обычно 30–60 секунд.</i>",
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
    dp = _build_dispatcher()
    logger.info(
        "Telegram-бот запущен (long polling). Менеджеров: %s",
        len(settings.allowed_telegram_user_ids),
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
