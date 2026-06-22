"""Провайдер Groq.

Groq предоставляет OpenAI-совместимый API (бесплатный тариф, быстрый inference
на LPU), поэтому переиспользуем тот же openai-SDK, лишь меняя base_url и ключ.
Дефолтная модель — llama-3.3-70b-versatile (хороший русский + JSON-режим).
"""

from __future__ import annotations

from openai import OpenAI

from .base import LLMProvider

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


class GroqProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GROQ_MODEL,
        base_url: str = "https://api.groq.com/openai/v1",
    ) -> None:
        if not api_key:
            raise ValueError("GROQ_API_KEY не задан — заполните .env")
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content or "{}"
