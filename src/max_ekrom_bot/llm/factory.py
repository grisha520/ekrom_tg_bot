"""Фабрика LLM-провайдера по конфигу.

Сейчас реализован OpenAI. Заглушки yandex/gigachat подскажут, что нужно
дописать провайдер при переходе на РФ-комплаенс.
"""

from __future__ import annotations

from ..config import Settings, get_settings
from .base import LLMProvider
from .openai_provider import OpenAIProvider


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    provider = settings.llm_provider.lower()

    if provider == "openai":
        return OpenAIProvider(api_key=settings.openai_api_key, model=settings.llm_model)

    if provider in {"yandex", "gigachat"}:
        raise NotImplementedError(
            f"Провайдер '{provider}' ещё не реализован. "
            "Добавьте класс по образцу OpenAIProvider и подключите его здесь. "
            "Для прототипа используйте LLM_PROVIDER=openai."
        )

    raise ValueError(f"Неизвестный LLM_PROVIDER: {settings.llm_provider}")
