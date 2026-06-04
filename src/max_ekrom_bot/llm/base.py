"""Абстрактный интерфейс LLM-провайдера.

Единственная задача провайдера здесь — по системному и пользовательскому
промпту вернуть строку с JSON. Парсинг/валидация JSON — выше по стеку.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Минимальный интерфейс, которого достаточно для разбора сообщений."""

    @abstractmethod
    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        """Вернуть ответ модели в виде строки (ожидается валидный JSON).

        Реализация должна, по возможности, включать режим строгого JSON-вывода
        (response_format / json mode), чтобы ответ гарантированно парсился.
        """
        raise NotImplementedError
