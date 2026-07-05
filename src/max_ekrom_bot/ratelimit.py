"""Простой лимитер запросов на пользователя (in-memory, скользящее окно).

Нужен в клиентском режиме: бот открыт всем, и без лимита спам/перебор быстро
выжрет дневной бюджет LLM-токенов у реальных клиентов. Храним метки времени
обращений каждого пользователя и считаем за час и за сутки.

In-memory достаточно: бот — один процесс (long polling). При перезапуске счётчики
сбрасываются — это приемлемо. Если позже появится несколько процессов, заменим
на Redis за тем же интерфейсом.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

_HOUR = 3600.0
_DAY = 86400.0


class RateLimiter:
    def __init__(self, per_hour: int = 0, per_day: int = 0) -> None:
        # 0 в любом из лимитов означает «без ограничения» по этому окну.
        self._per_hour = per_hour
        self._per_day = per_day
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def _prune(self, dq: deque[float], now: float) -> None:
        while dq and now - dq[0] > _DAY:
            dq.popleft()

    def allowed(self, uid: int, now: float | None = None) -> bool:
        """Можно ли обслужить запрос (не превышены лимиты час/сутки)."""
        now = time.time() if now is None else now
        dq = self._hits[uid]
        self._prune(dq, now)
        if self._per_day and len(dq) >= self._per_day:
            return False
        if self._per_hour:
            in_hour = sum(1 for t in dq if now - t <= _HOUR)
            if in_hour >= self._per_hour:
                return False
        return True

    def hit(self, uid: int, now: float | None = None) -> None:
        """Зафиксировать обращение (вызывать после allowed()==True)."""
        now = time.time() if now is None else now
        self._hits[uid].append(now)

    def retry_after(self, uid: int, now: float | None = None) -> float:
        """Секунд до момента, когда запрос снова будет разрешён (0 — уже можно).

        Если блокируют оба окна (час и сутки) — берём больший срок ожидания.
        """
        now = time.time() if now is None else now
        dq = self._hits[uid]
        self._prune(dq, now)
        waits: list[float] = []
        if self._per_day and len(dq) >= self._per_day:
            # ждём, пока самый старый запрос выпадет из суточного окна
            waits.append(dq[0] + _DAY - now)
        if self._per_hour:
            in_hour = [t for t in dq if now - t <= _HOUR]
            if len(in_hour) >= self._per_hour:
                waits.append(in_hour[0] + _HOUR - now)
        return max(0.0, max(waits)) if waits else 0.0
