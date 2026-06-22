"""Фабрика LLM-провайдера по конфигу.

Реализованы OpenAI и Groq (бесплатный, OpenAI-совместимый). Заглушки
yandex/gigachat подскажут, что нужно дописать провайдер при переходе на
РФ-комплаенс.
"""

from __future__ import annotations

from ..config import Settings, get_settings
from .base import LLMProvider
from .groq_provider import DEFAULT_GROQ_MODEL, GroqProvider
from .openai_provider import OpenAIProvider


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    provider = settings.llm_provider.lower()

    if provider == "openai":
        return OpenAIProvider(api_key=settings.openai_api_key, model=settings.llm_model)

    if provider == "groq":
        # LLM_MODEL по умолчанию рассчитан на OpenAI (gpt-*); для Groq берём
        # его только если это не OpenAI-модель, иначе — дефолтную модель Groq.
        model = settings.llm_model
        if not model or model.lower().startswith("gpt-"):
            model = DEFAULT_GROQ_MODEL
        return GroqProvider(
            api_key=settings.groq_api_key,
            model=model,
            base_url=settings.groq_base_url,
        )

    if provider in {"yandex", "gigachat"}:
        raise NotImplementedError(
            f"Провайдер '{provider}' ещё не реализован. "
            "Добавьте класс по образцу OpenAIProvider и подключите его здесь. "
            "Для прототипа используйте LLM_PROVIDER=openai."
        )

    raise ValueError(f"Неизвестный LLM_PROVIDER: {settings.llm_provider}")
