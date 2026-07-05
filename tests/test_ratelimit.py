"""Тесты лимитера запросов на пользователя."""

from __future__ import annotations

import pytest

from max_ekrom_bot.ratelimit import RateLimiter


def test_per_hour_limit():
    rl = RateLimiter(per_hour=3, per_day=0)
    t = 1000.0
    for _ in range(3):
        assert rl.allowed(1, now=t)
        rl.hit(1, now=t)
    assert not rl.allowed(1, now=t)  # 4-й в тот же час — нельзя
    # через час окно освобождается
    assert rl.allowed(1, now=t + 3601)


def test_per_day_limit():
    rl = RateLimiter(per_hour=0, per_day=5)
    t = 0.0
    for i in range(5):
        assert rl.allowed(1, now=t + i * 1000)  # разнесены по времени (разные часы не важны)
        rl.hit(1, now=t + i * 1000)
    assert not rl.allowed(1, now=t + 6000)
    # на следующие сутки — снова можно
    assert rl.allowed(1, now=t + 86401 + 5000)


def test_zero_means_unlimited():
    rl = RateLimiter(per_hour=0, per_day=0)
    for i in range(100):
        assert rl.allowed(1, now=float(i))
        rl.hit(1, now=float(i))


def test_users_independent():
    rl = RateLimiter(per_hour=1, per_day=0)
    rl.hit(1, now=1.0)
    assert not rl.allowed(1, now=1.0)
    assert rl.allowed(2, now=1.0)  # другой пользователь не затронут


def test_retry_after_hour_window():
    rl = RateLimiter(per_hour=2, per_day=0)
    rl.hit(1, now=100.0)
    rl.hit(1, now=150.0)
    assert not rl.allowed(1, now=200.0)
    # первый запрос выпадет из часа в 100+3600=3700 → ждать 3500 с
    assert rl.retry_after(1, now=200.0) == pytest.approx(3500.0)
    # когда снова можно — retry_after 0
    assert rl.retry_after(1, now=3800.0) == 0.0


def test_retry_after_zero_when_allowed():
    rl = RateLimiter(per_hour=5, per_day=0)
    rl.hit(1, now=0.0)
    assert rl.retry_after(1, now=10.0) == 0.0


def test_retry_after_takes_longer_of_two_windows():
    rl = RateLimiter(per_hour=1, per_day=2)
    rl.hit(1, now=0.0)      # блокирует час
    # только час блокирует (в сутки 1<2) → ждать до 3600
    assert rl.retry_after(1, now=100.0) == pytest.approx(3500.0)
