"""Тесты выбора категории моделью (Фаза 2). LLM подменяем фейком."""

from __future__ import annotations

import pytest

from max_ekrom_bot.config import Settings
from max_ekrom_bot.models import ProductSpec
from max_ekrom_bot.parsing.category_resolver import CategoryResolver, load_categories


class FakeLLM:
    def __init__(self, response: str = "{}", raise_exc: bool = False) -> None:
        self.response = response
        self.raise_exc = raise_exc

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        if self.raise_exc:
            raise RuntimeError("boom")
        return self.response


def _resolver(llm: FakeLLM) -> CategoryResolver:
    r = CategoryResolver(settings=Settings(LLM_PROVIDER="rule"))
    r._llm = llm
    return r


# Снимок категорий должен существовать в репозитории.
def test_categories_snapshot_loaded():
    assert len(load_categories()) > 0


def test_resolver_sets_category_from_llm():
    r = _resolver(FakeLLM('{"map": {"1": 3}}'))
    assert r._cats, "нет categories.json"
    spec = ProductSpec(raw_text="чайник")
    r.resolve([spec])
    assert spec.category_url == r._cats[2]["url"]
    assert spec.category_name == r._cats[2]["name"]


def test_resolver_zero_means_no_category():
    r = _resolver(FakeLLM('{"map": {"1": 0}}'))
    spec = ProductSpec(raw_text="нечто несуществующее")
    r.resolve([spec])
    assert spec.category_url is None


def test_resolver_index_out_of_range_ignored():
    r = _resolver(FakeLLM('{"map": {"1": 99999}}'))
    spec = ProductSpec(raw_text="чайник")
    r.resolve([spec])
    assert spec.category_url is None


@pytest.mark.parametrize("llm", [FakeLLM("не json"), FakeLLM(raise_exc=True)])
def test_resolver_failures_leave_category_none(llm):
    r = _resolver(llm)
    spec = ProductSpec(raw_text="чайник")
    r.resolve([spec])
    assert spec.category_url is None
