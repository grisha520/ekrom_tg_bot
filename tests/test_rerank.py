"""Тесты LLM-ре-ранка шортлиста (#6): построение промпта и фолбэк-логика.

Сетевые вызовы не делаем — подставляем фейковый LLM-провайдер.
"""

from __future__ import annotations

import asyncio

from max_ekrom_bot.config import Settings
from max_ekrom_bot.models import ApplianceType, CatalogProduct, ProductSpec
from max_ekrom_bot.pipeline import Pipeline, _candidate_brief, _rerank_prompts


class FakeLLM:
    def __init__(self, response: str = "{}", raise_exc: bool = False) -> None:
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.raise_exc:
            raise RuntimeError("boom")
        return self.response


def _pipeline_with_llm(llm: FakeLLM | None) -> Pipeline:
    # LLM_PROVIDER=rule → парсер без сети; LLM для ре-ранка подменяем напрямую.
    p = Pipeline(Settings(LLM_PROVIDER="rule"))
    p._llm = llm
    return p


def _p(title: str, price: float, attrs: dict[str, str] | None = None) -> CatalogProduct:
    return CatalogProduct(title=title, url=f"u/{title}", price=price, attributes=attrs or {})


PRODUCTS = [
    _p("Духовка A белая", 100, {"Цвет": "Белый"}),
    _p("Духовка B чёрная", 120, {"Цвет": "Чёрный"}),
    _p("Духовка C белая", 140, {"Цвет": "Белый"}),
]


# ── Построение промпта ───────────────────────────────────────────────────────

def test_candidate_brief_includes_title_price_attrs():
    brief = _candidate_brief(_p("Духовка X", 1000, {"Цвет": "Белый", "Ширина, мм": "595"}))
    assert "Духовка X" in brief
    assert "1000" in brief
    assert "Цвет: Белый" in brief


def test_rerank_prompt_lists_requirements_and_candidates():
    spec = ProductSpec(appliance=ApplianceType.OVEN, color="белый", width_mm=600)
    system, user = _rerank_prompts(spec, PRODUCTS, "средний")
    assert "JSON" in system
    assert "Цвет: белый" in user
    assert "Ценовой сегмент: средний" in user
    assert "Вариант 1:" in user and "Вариант 3:" in user


# ── Фолбэк-логика _llm_pick ──────────────────────────────────────────────────

def _pick(llm: FakeLLM):
    p = _pipeline_with_llm(llm)
    return asyncio.run(p._llm_pick(ProductSpec(appliance=ApplianceType.OVEN), PRODUCTS, "средний"))


def test_llm_pick_valid_choice():
    idx, reason = _pick(FakeLLM('{"index": 2, "reason": "лучший по цвету"}'))
    assert idx == 1  # вариант 2 (1-based) → индекс 1
    assert reason == "лучший по цвету"


def test_llm_pick_invalid_json_falls_back():
    idx, reason = _pick(FakeLLM("не json вовсе"))
    assert (idx, reason) == (0, None)


def test_llm_pick_index_out_of_range_falls_back():
    idx, reason = _pick(FakeLLM('{"index": 99}'))
    assert (idx, reason) == (0, None)


def test_llm_pick_missing_index_falls_back():
    idx, reason = _pick(FakeLLM('{"reason": "без индекса"}'))
    assert (idx, reason) == (0, None)


def test_llm_pick_exception_falls_back():
    idx, reason = _pick(FakeLLM(raise_exc=True))
    assert (idx, reason) == (0, None)


def test_rerank_flag_defaults_off():
    assert Settings(LLM_PROVIDER="rule").llm_rerank is False
