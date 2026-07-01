"""Тесты фабрик парсера и LLM-провайдера (выбор по конфигу, без сети)."""

from __future__ import annotations

import pytest

from max_ekrom_bot.config import Settings
from max_ekrom_bot.llm.factory import get_llm_provider
from max_ekrom_bot.llm.groq_provider import GroqProvider
from max_ekrom_bot.llm.openai_provider import OpenAIProvider
from max_ekrom_bot.parsing.factory import get_parser
from max_ekrom_bot.parsing.rule_parser import RuleBasedParser


def test_rule_provider_returns_rule_parser():
    settings = Settings(LLM_PROVIDER="rule")
    assert isinstance(get_parser(settings), RuleBasedParser)


def test_llm_parser_uses_passed_settings_not_global(monkeypatch):
    # Глобальные настройки сломаны намеренно: если фабрика возьмёт их вместо
    # переданных — конструктор провайдера упадёт. Должно использоваться settings.
    monkeypatch.setenv("OPENAI_API_KEY", "")
    settings = Settings(LLM_PROVIDER="openai", OPENAI_API_KEY="sk-passed", LLM_MODEL="gpt-4o-mini")
    parser = get_parser(settings)  # не должно бросить ValueError про пустой ключ
    assert parser is not None


def test_llm_factory_selects_groq():
    settings = Settings(LLM_PROVIDER="groq", GROQ_API_KEY="gk-x")
    assert isinstance(get_llm_provider(settings), GroqProvider)


def test_llm_factory_selects_openai():
    settings = Settings(LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x")
    assert isinstance(get_llm_provider(settings), OpenAIProvider)


def test_groq_defaults_model_when_openai_model_configured():
    # LLM_MODEL=gpt-* не подходит Groq → должна подставиться дефолтная модель Groq.
    settings = Settings(LLM_PROVIDER="groq", GROQ_API_KEY="gk-x", LLM_MODEL="gpt-4o-mini")
    provider = get_llm_provider(settings)
    assert provider._model == "openai/gpt-oss-120b"


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_llm_provider(Settings(LLM_PROVIDER="totally-unknown"))


def test_gigachat_not_implemented():
    with pytest.raises(NotImplementedError):
        get_llm_provider(Settings(LLM_PROVIDER="gigachat"))


def test_model_override_applies():
    # Override модели (для резолвера) должен победить дефолт провайдера.
    s = Settings(LLM_PROVIDER="yandex", YANDEX_API_KEY="k", YANDEX_FOLDER_ID="b1")
    p = get_llm_provider(s, model="yandexgpt")
    assert p._model_uri.endswith("/yandexgpt/latest")
