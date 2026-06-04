"""Клиент Bot API МАКС: отправка текста и файлов (PDF) пользователю.

Документация: https://dev.max.ru. Токен передаётся query-параметром
access_token. Отправка файла в МАКС — двухшаговая: получить upload URL,
загрузить файл, затем отправить сообщение с вложением (token attachment).

Точные пути/поля помечены TODO(api) и проверяются по актуальной доке dev.max.ru.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from ..config import Settings, get_settings


class MaxClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.max_bot_token:
            raise ValueError("MAX_BOT_TOKEN не задан — заполните .env")
        self._base = self._settings.max_api_base_url.rstrip("/")
        self._token = self._settings.max_bot_token

    def _params(self, extra: dict | None = None) -> dict:
        params = {"access_token": self._token}
        if extra:
            params.update(extra)
        return params

    async def send_text(self, chat_id: int | str, text: str) -> dict:
        """Отправить текстовое сообщение в чат."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/messages",
                params=self._params({"chat_id": chat_id}),
                json={"text": text},
            )
            resp.raise_for_status()
            return resp.json()

    async def send_pdf(self, chat_id: int | str, pdf_path: Path, caption: str = "") -> dict:
        """Отправить PDF-файл клиенту.

        Шаги (см. dev.max.ru → Uploading files):
          1) GET upload URL для типа 'file';
          2) PUT/POST файл на этот URL → получить token;
          3) POST /messages с attachment {type:'file', payload:{token}}.
        """
        async with httpx.AsyncClient(timeout=120) as client:
            # 1) Получить URL для загрузки
            up = await client.post(
                f"{self._base}/uploads",
                params=self._params({"type": "file"}),  # TODO(api): подтвердить тип
            )
            up.raise_for_status()
            upload_url = up.json()["url"]

            # 2) Загрузить файл
            with pdf_path.open("rb") as fh:
                files = {"data": (pdf_path.name, fh, "application/pdf")}
                up2 = await client.post(upload_url, files=files)
                up2.raise_for_status()
                token = up2.json().get("token") or up2.json()  # TODO(api): уточнить поле

            # 3) Отправить сообщение с вложением
            payload = {
                "text": caption,
                "attachments": [{"type": "file", "payload": {"token": token}}],
            }
            resp = await client.post(
                f"{self._base}/messages",
                params=self._params({"chat_id": chat_id}),
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
