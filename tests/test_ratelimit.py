"""Тесты лимитера запросов на пользователя."""

from __future__ import annotations

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
