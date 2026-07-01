"""Сопоставление позиций запроса с категориями каталога ekrom (через LLM).

Категории берутся из data/categories.json (снимок дерева, см.
scripts/build_categories.py). Модель по тексту позиции выбирает подходящую
категорию — это расширяет подбор за пределы 6 встроенных типов техники.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

from ..config import REPO_ROOT, Settings, get_settings
from ..llm import LLMProvider
from ..models import ProductSpec

logger = logging.getLogger(__name__)

_SYSTEM = """\
Ты сопоставляешь позиции запроса клиента с категориями каталога бытовой техники.
Тебе дан нумерованный список КАТЕГОРИЙ (с путём-разделом) и нумерованный список
ПОЗИЦИЙ запроса. Для каждой позиции верни номер наиболее подходящей категории,
либо 0, если подходящей нет.

Правила:
- Выбирай по НАЗНАЧЕНИЮ товара и его РАЗДЕЛУ (путь категории), а НЕ по созвучию
  слов. Пример: «утюг с парогенератором» — это уход за одеждой
  (Парогенераторы/Гладильные системы), а НЕ кухонная «паровая духовка».
- Не опирайся на цвет/бренд — только тип товара.
- Если тип установки (встраиваемая/отдельностоящая) НЕ указан — выбирай ОБЩУЮ
  категорию товара, а не подкатегории «Встраиваемые»/«Отдельностоящие».

Ответ — СТРОГО JSON: {"map": {"1": <номер категории>, "2": <номер>, ...}}.
Никакого текста, кроме JSON."""


@lru_cache(maxsize=1)
def load_categories() -> tuple[dict, ...]:
    """Загрузить снимок категорий (кэшируется). Пусто, если файла нет."""
    path = REPO_ROOT / "data" / "categories.json"
    if not path.exists():
        logger.warning("data/categories.json не найден — выбор категории отключён")
        return ()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return tuple(data)
    except Exception:  # noqa: BLE001
        logger.exception("не удалось прочитать categories.json")
        return ()


def _label(cat: dict) -> str:
    path = cat.get("path") or ""
    return f"{path} / {cat['name']}" if path else cat["name"]


def _top_level_categories(cats: tuple[dict, ...]) -> tuple[dict, ...]:
    """Оставить только родительские категории (убрать подкатегории, чьё URL
    вложено в другую категорию списка).

    Подкатегории на ekrom часто имеют обезличенные имена («Отдельностоящие»,
    «Встраиваемые», «Комбинированные») и дублируются под многими разделами —
    это путает LLM при выборе. Родительские имена различимы («Винные шкафы»,
    «Морозильные камеры»), а конкретный подтип (встраив./отдельностоящий) потом
    доберёт селектор по характеристикам товара.
    """
    urls = [c["url"].rstrip("/") for c in cats if c.get("url")]
    kept: list[dict] = []
    for c in cats:
        u = (c.get("url") or "").rstrip("/")
        if not u:
            continue
        if any(u != other and u.startswith(other + "/") for other in urls):
            continue  # это подкатегория другой категории — пропускаем
        kept.append(c)
    return tuple(kept)


class CategoryResolver:
    """Заполняет category_url/category_name у позиций через один вызов LLM."""

    def __init__(self, llm: LLMProvider | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._llm = llm
        # Только родительские категории — различимые имена, меньше путаницы у LLM.
        self._cats = _top_level_categories(load_categories())

    def _get_llm(self) -> LLMProvider:
        if self._llm is None:
            from ..llm import get_llm_provider

            self._llm = get_llm_provider(self._settings)
        return self._llm

    def resolve(self, specs: list[ProductSpec]) -> None:
        """Проставить category_url/category_name. Ошибки не критичны (остаётся None)."""
        if not self._cats or not specs:
            return
        cat_lines = [f"{i}. {_label(c)}" for i, c in enumerate(self._cats, 1)]
        req_lines = [f"{i}. {s.raw_text or s.appliance.value}" for i, s in enumerate(specs, 1)]
        user = "КАТЕГОРИИ:\n" + "\n".join(cat_lines) + "\n\nПОЗИЦИИ:\n" + "\n".join(req_lines)

        try:
            raw = self._get_llm().complete_json(_SYSTEM, user)
            mapping = json.loads(raw).get("map", {})
        except Exception:  # noqa: BLE001 — выбор категории не критичен
            logger.warning("выбор категории через LLM не удался", exc_info=True)
            return

        for i, spec in enumerate(specs, 1):
            value = mapping.get(str(i), mapping.get(i))
            try:
                idx = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= idx <= len(self._cats):
                cat = self._cats[idx - 1]
                spec.category_url = cat["url"]
                spec.category_name = cat["name"]
