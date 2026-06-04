"""Провайдер OpenAI (для прототипа). Использует JSON-режим вывода."""

from __future__ import annotations

from openai import OpenAI

from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY не задан — заполните .env")
        self._client = OpenAI(api_key=api_key)
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
