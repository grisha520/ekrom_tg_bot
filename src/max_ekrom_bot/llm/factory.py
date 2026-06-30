"""Фабрика LLM-провайдера по конфигу.

Реализованы OpenAI, Groq (бесплатный, OpenAI-совместимый) и YandexGPT
(РФ-комплаенс, оплата в рублях). GigaChat — пока заглушка.
"""

from __future__ import annotations

from ..config import Settings, get_settings
from .base import LLMProvider
from .groq_provider import DEFAULT_GROQ_MODEL, GroqProvider
from .openai_provider import OpenAIProvider
from .yandex_provider import YandexGPTProvider


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

    if provider == "yandex":
        # Используем LLM_MODEL, только если это yandex-модель (yandexgpt-lite/
        # yandexgpt/…). Иначе (там может остаться gpt-*/openai-модель от другого
        # провайдера) — берём lite по умолчанию.
        model = settings.llm_model
        if not model or not model.lower().startswith("yandexgpt"):
            model = "yandexgpt-lite"
        return YandexGPTProvider(
            api_key=settings.yandex_api_key,
            folder_id=settings.yandex_folder_id,
            model=model,
        )

    if provider == "gigachat":
        raise NotImplementedError(
            "Провайдер 'gigachat' ещё не реализован. Добавьте класс по образцу "
            "YandexGPTProvider и подключите его здесь."
        )

    raise ValueError(f"Неизвестный LLM_PROVIDER: {settings.llm_provider}")
