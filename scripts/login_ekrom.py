"""Авторизация на ekrom.ru → сохранение сессии (storage_state).

Капчи в форме входа НЕТ (подтверждено разведкой), поэтому возможны два режима:

1. АВТО: если в .env заданы EKROM_LOGIN и EKROM_PASSWORD — скрипт сам войдёт
   (можно headless) и сохранит сессию.
2. РУЧНОЙ: если учётка не задана — откроется браузер, вы войдёте сами,
   вернётесь в консоль и нажмёте Enter.

Сессия сохраняется в data/storage_state.json (в .gitignore — НЕ коммитить).

Запуск:
  python scripts/login_ekrom.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from max_ekrom_bot.config import get_settings  # noqa: E402
from max_ekrom_bot.ekrom.client import EkromClient  # noqa: E402


async def main() -> None:
    settings = get_settings()
    has_creds = bool(settings.ekrom_login and settings.ekrom_password)

    if has_creds:
        # АВТО-режим: вход без участия пользователя.
        async with EkromClient(settings) as client:
            await client.login()
            state_path = await client.save_storage_state()
        print(f"\nВход выполнен автоматически. Сессия сохранена: {state_path}")
        return

    # РУЧНОЙ режим: открываем headful-браузер и ждём действий пользователя.
    from playwright.async_api import async_playwright  # локальный импорт

    auth_url = f"{settings.ekrom_base_url.rstrip('/')}/auth/"
    state_path = settings.storage_state_path
    state_path.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(auth_url)

        print("\n" + "=" * 60)
        print("EKROM_LOGIN/EKROM_PASSWORD не заданы — войдите вручную.")
        print("Войдите в партнёрский кабинет в открывшемся браузере,")
        print("затем вернитесь сюда и нажмите Enter.")
        print("=" * 60 + "\n")

        await asyncio.get_event_loop().run_in_executor(None, input, "Нажмите Enter после входа... ")

        await context.storage_state(path=str(state_path))
        print(f"\nСессия сохранена: {state_path}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
