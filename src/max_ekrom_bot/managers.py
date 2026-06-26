"""Хранилище менеджеров (allowlist) с правкой во время работы — без рестарта.

Список менеджеров живёт в data/managers.json и меняется прямо из бота (добавление/
удаление). На первом запуске наполняется из TELEGRAM_ALLOWED_IDS (.env), чтобы
существующие менеджеры сохранились.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ManagerStore:
    """Множество разрешённых менеджеров (id → подпись), персистентное в JSON."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._items: dict[int, str] = self._load()

    def _load(self) -> dict[int, str]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return {int(k): str(v) for k, v in raw.items()}
        except Exception:  # noqa: BLE001 — битый файл не должен ронять бота
            logger.exception("не удалось прочитать %s", self._path)
            return {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {str(k): v for k, v in sorted(self._items.items())}
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def seed(self, ids) -> None:
        """Первичное наполнение из .env, если файла/записей ещё нет."""
        if not self._items and ids:
            self._items = {int(i): str(i) for i in ids}
            self._save()

    def ids(self) -> set[int]:
        return set(self._items)

    def items(self) -> dict[int, str]:
        return dict(self._items)

    def contains(self, uid: int) -> bool:
        return uid in self._items

    def add(self, uid: int, label: str | None = None) -> bool:
        if uid in self._items:
            return False
        self._items[uid] = label or str(uid)
        self._save()
        return True

    def remove(self, uid: int) -> bool:
        if uid not in self._items:
            return False
        del self._items[uid]
        self._save()
        return True
