"""Тесты провайдера YandexGPT (без сети — httpx.post мокается)."""

from __future__ import annotations

import json

import pytest

from max_ekrom_bot.config import Settings
from max_ekrom_bot.llm import yandex_provider as yp
from max_ekrom_bot.llm.factory import get_llm_provider
from max_ekrom_bot.llm.yandex_provider import YandexGPTProvider, _extract_json


def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_strips_markdown_fence():
    raw = "```json\n{\"specs\": []}\n```"
    assert json.loads(_extract_json(raw)) == {"specs": []}


def test_extract_json_trims_surrounding_text():
    raw = "Вот ответ: {\"x\": 2} — готово"
    assert json.loads(_extract_json(raw)) == {"x": 2}


def test_model_uri_built_from_folder():
    p = YandexGPTProvider(api_key="k", folder_id="b1xxx", model="yandexgpt-lite")
    assert p._model_uri == "gpt://b1xxx/yandexgpt-lite/latest"


def test_requires_keys():
    with pytest.raises(ValueError):
        YandexGPTProvider(api_key="", folder_id="")


def test_factory_returns_yandex_with_lite_default():
    # llm_model по умолчанию gpt-* (openai) → для yandex берём yandexgpt-lite
    s = Settings(LLM_PROVIDER="yandex", YANDEX_API_KEY="k", YANDEX_FOLDER_ID="b1")
    p = get_llm_provider(s)
    assert isinstance(p, YandexGPTProvider)
    assert p._model_uri.endswith("/yandexgpt-lite/latest")


def test_complete_json_parses_response(monkeypatch):
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "result": {"alternatives": [{"message": {"text": '{"ok": true}'}}]}
            }

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
        captured["url"] = url
        captured["body"] = json
        captured["auth"] = headers["Authorization"]
        return _Resp()

    monkeypatch.setattr(yp.httpx, "post", fake_post)
    p = YandexGPTProvider(api_key="secret", folder_id="b1", model="yandexgpt-lite")
    out = p.complete_json("sys", "user")
    assert out == '{"ok": true}'
    assert captured["url"].endswith("/foundationModels/v1/completion")
    assert captured["auth"] == "Api-Key secret"
    assert captured["body"]["messages"][0] == {"role": "system", "text": "sys"}
