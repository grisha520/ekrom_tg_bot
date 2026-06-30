"""Провайдер YandexGPT (Yandex Cloud Foundation Models).

РФ-комплаенс: данные обрабатываются в Яндекс Облаке (РФ), оплата в рублях. Удобно
для клиентского бота (физлица, 152-ФЗ). Синхронный REST-вызов — `complete_json`
вызывается из пайплайна в отдельном потоке, блокировки event loop нет.

Авторизация: заголовок `Authorization: Api-Key <ключ>` (API-ключ сервисного
аккаунта с ролью ai.languageModels.user). Модель задаётся через modelUri вида
`gpt://<folder_id>/yandexgpt-lite/latest`.
"""

from __future__ import annotations

import httpx

from .base import LLMProvider

COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


def _extract_json(text: str) -> str:
    """Вынуть JSON из ответа модели (снять ```-ограждение и обрезать до объекта).

    YandexGPT не всегда поддерживает строгий JSON-режим и может обрамить ответ
    markdown-блоком или добавить текст. Парсер выше делает json.loads, поэтому
    подстрахуемся: берём подстроку от первой `{` до последней `}`.
    """
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t[:4].lower() == "json":
            t = t[4:].strip()
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start : end + 1]
    return t


class YandexGPTProvider(LLMProvider):
    def __init__(
        self, api_key: str, folder_id: str, model: str = "yandexgpt-lite"
    ) -> None:
        if not api_key or not folder_id:
            raise ValueError(
                "YANDEX_API_KEY и YANDEX_FOLDER_ID не заданы — заполните .env"
            )
        self._headers = {
            "Authorization": f"Api-Key {api_key}",
            "Content-Type": "application/json",
        }
        self._model_uri = f"gpt://{folder_id}/{model}/latest"

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        body = {
            "modelUri": self._model_uri,
            "completionOptions": {
                "stream": False,
                "temperature": 0,
                "maxTokens": "2000",
            },
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": user_prompt},
            ],
        }
        resp = httpx.post(COMPLETION_URL, headers=self._headers, json=body, timeout=60.0)
        resp.raise_for_status()
        data = resp.json()
        text = data["result"]["alternatives"][0]["message"]["text"]
        return _extract_json(text)
