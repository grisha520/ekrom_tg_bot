"""Выбор парсера по конфигу.

LLM_PROVIDER=rule   → RuleBasedParser (без внешнего API).
LLM_PROVIDER=openai → RequestParser поверх OpenAI.
"""

from __future__ import annotations

from typing import Protocol

from ..config import Settings, get_settings
from ..models import CustomerRequest


class Parser(Protocol):
    def parse(self, message: str) -> CustomerRequest: ...


def get_parser(settings: Settings | None = None) -> Parser:
    settings = settings or get_settings()
    if settings.llm_provider.lower() == "rule":
        from .rule_parser import RuleBasedParser

        return RuleBasedParser()

    from ..llm import get_llm_provider
    from .parser import RequestParser

    # Пробрасываем те же settings в провайдера, иначе RequestParser молча
    # возьмёт глобальный .env (важно, когда Pipeline создан с кастомными settings).
    return RequestParser(get_llm_provider(settings))
