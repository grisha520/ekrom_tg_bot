"""Тесты /preview: оператор в предпросмотре выглядит клиентом для UX-решений."""

from __future__ import annotations

import max_ekrom_bot.telegram_app as app


def test_acts_as_operator_follows_real_role(monkeypatch):
    monkeypatch.setattr(app, "_is_operator", lambda uid: uid == 1)
    monkeypatch.setattr(app, "_PREVIEW", set())
    assert app._acts_as_operator(1) is True
    assert app._acts_as_operator(2) is False


def test_preview_makes_operator_look_like_client(monkeypatch):
    monkeypatch.setattr(app, "_is_operator", lambda uid: uid == 1)
    monkeypatch.setattr(app, "_PREVIEW", {1})
    assert app._acts_as_operator(1) is False  # в предпросмотре — клиентский UX
    # но реальные права не отняты
    assert app._is_operator(1) is True


def test_preview_does_not_grant_operator_to_client(monkeypatch):
    # Клиент в _PREVIEW (теоретически) оператором не становится.
    monkeypatch.setattr(app, "_is_operator", lambda uid: False)
    monkeypatch.setattr(app, "_PREVIEW", {5})
    assert app._acts_as_operator(5) is False
