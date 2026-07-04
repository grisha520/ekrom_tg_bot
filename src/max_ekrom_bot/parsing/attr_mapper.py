"""Схемо-маппер: запрос клиента → ограничения по РЕАЛЬНЫМ атрибутам категории.

Отдаём LLM (Pro) настоящие атрибуты категории (имя, тип, значения/диапазон) и
текст позиции; модель выбирает подходящие атрибуты ИЗ СПИСКА и возвращает
ограничения с операторами (eq/gte/lte/approx). За счёт опоры на реальную схему
исключаются ошибки угадывания имён и значений.
"""

from __future__ import annotations

import json
import logging

from ..config import Settings, get_settings
from ..llm import LLMProvider
from ..models import AttrConstraint, ProductSpec

logger = logging.getLogger(__name__)

_SYSTEM = """\
Ты сопоставляешь запрос клиента с ХАРАКТЕРИСТИКАМИ товара. Для каждой ПОЗИЦИИ дан
список доступных атрибутов её категории (имя, тип, значения/диапазон). Верни для
позиции ограничения ТОЛЬКО по тем атрибутам, которые клиент реально упомянул.

Правила:
- attribute бери ДОСЛОВНО из списка атрибутов позиции (не придумывай своих имён).
- Если клиент не упомянул характеристику из списка — не добавляй её.
- op: "eq" — точное/категориальное ("фронтальная загрузка", "сенсорное"),
  "gte" — «от N / не менее N», "lte" — «до N / не более N / не громче N»,
  "approx" — примерное число без «от/до» («на 300 литров», «около 8 кг»).
- Для категориальных value бери из списка значений атрибута (ближайшее по смыслу).
- Цвет, бренд, цену, тип техники, число конфорок НЕ включай (они уже разобраны).

Пример. Позиция: «стиралка на 8 кг с вертикальной загрузкой, отжим не менее 1200,
тихая до 45 дБ». Атрибуты: «Тип загрузки» (значения: Фронтальный, Вертикальный),
«Max загрузка белья, кг» (число), «Скорость отжима, оборотов/мин» (число),
«Уровень шума, дБ» (число). Ответ для позиции:
[{"attribute":"Тип загрузки","op":"eq","value":"Вертикальный"},
 {"attribute":"Max загрузка белья, кг","op":"approx","value":"8"},
 {"attribute":"Скорость отжима, оборотов/мин","op":"gte","value":"1200"},
 {"attribute":"Уровень шума, дБ","op":"lte","value":"45"}]

Ответ — СТРОГО JSON: {"map": {"1": [{"attribute": "...", "op": "...", "value": "..."}], "2": [...]}}
Пустой список, если по позиции ограничений нет. Никакого текста кроме JSON."""


def _schema_lines(schema: dict[str, dict]) -> str:
    lines = []
    for name, info in schema.items():
        t = info.get("type")
        if t == "number":
            unit = f" {info['unit']}" if info.get("unit") else ""
            desc = f"число ({info.get('min')}–{info.get('max')}{unit})"
        elif t == "energy":
            desc = "класс энергопотребления (A+++…G)"
        else:
            vals = ", ".join(map(str, info.get("values", [])[:12]))
            desc = f"значения: {vals}"
        lines.append(f"  - {name}: {desc}")
    return "\n".join(lines)


class AttrMapper:
    def __init__(self, llm: LLMProvider | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._llm = llm

    def _get_llm(self) -> LLMProvider:
        if self._llm is None:
            from ..llm import get_llm_provider

            # Как резолвер — берём более сильную модель (важна точность выбора).
            self._llm = get_llm_provider(
                self._settings, model=self._settings.resolver_model or None
            )
        return self._llm

    def map(self, items: list[tuple[ProductSpec, dict]], original_message: str = "") -> None:
        """Заполнить attr_constraints у позиций (items = [(spec, schema)]).

        Один вызов LLM на все позиции со схемой. Ошибки не критичны (остаётся
        свободный extra как запасной механизм сопоставления).

        original_message — полный запрос клиента: парсер часто обрезает raw_text до
        «стиральная машина», теряя характеристики; для ОДНОЙ позиции берём полный
        текст, для нескольких — raw_text каждой (+ полный запрос как контекст)."""
        indexed = [(i, s, sch) for i, (s, sch) in enumerate(items, 1) if sch]
        if not indexed:
            return
        single = len(indexed) == 1
        blocks = []
        for i, spec, schema in indexed:
            text = original_message if (single and original_message) else (
                spec.raw_text or spec.appliance.value)
            blocks.append(
                f"ПОЗИЦИЯ {i}: {text}\n"
                f"Атрибуты категории:\n{_schema_lines(schema)}"
            )
        prefix = (
            f"Полный запрос клиента: {original_message}\n\n"
            if original_message and not single else ""
        )
        user = prefix + "\n\n".join(blocks)
        try:
            raw = self._get_llm().complete_json(_SYSTEM, user)
            mapping = json.loads(raw).get("map", {})
        except Exception:  # noqa: BLE001 — маппер не критичен
            logger.warning("схемо-маппер не удался", exc_info=True)
            return

        for i, spec, schema in indexed:
            items_for_pos = mapping.get(str(i), mapping.get(i)) or []
            valid_keys = set(schema)
            constraints = []
            for c in items_for_pos:
                if not (isinstance(c, dict) and c.get("attribute") in valid_keys
                        and str(c.get("value") or "").strip()):
                    continue  # только реальные атрибуты категории с непустым значением
                try:
                    constraints.append(AttrConstraint(**c))
                except Exception:  # noqa: BLE001 — кривой элемент пропускаем
                    continue
            if constraints:
                spec.attr_constraints = constraints
                spec.extra = {}  # маппер точнее свободного extra — заменяем
