"""Слой LLM: абстракция провайдера + конкретные реализации.

Провайдер выбирается по конфигу (LLM_PROVIDER), поэтому остальной код
не зависит от конкретного вендора и его можно заменить (OpenAI ↔ YandexGPT ↔ GigaChat).
"""

from .base import LLMProvider
from .factory import get_llm_provider

__all__ = ["LLMProvider", "get_llm_provider"]
