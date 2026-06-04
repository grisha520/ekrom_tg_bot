"""Управление сохранённой сессией ekrom (storage_state с cookies).

Стратегия обхода капчи: один раз логинимся вручную в реальном браузере
(scripts/login_ekrom.py), сохраняем storage_state.json и переиспользуем
его во всех headless-запусках. Когда сессия протухнет — логинимся заново.
"""

from __future__ import annotations

from pathlib import Path


def storage_state_exists(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
