"""Парсер сообщений клиента: текст → CustomerRequest (через LLM).

LLM возвращает строгий JSON, который валидируется в доменные модели.
Если LLM недоступен/вернул мусор — поднимаем понятную ошибку выше.
"""

from __future__ import annotations

import json

from ..llm import LLMProvider, get_llm_provider
from ..models import CustomerRequest

SYSTEM_PROMPT = """\
Ты — ассистент по подбору встраиваемой бытовой техники в магазине ekrom.ru.
Тебе приходит свободное сообщение клиента со списком желаемой техники.
Твоя задача — разобрать его в строгий JSON по схеме ниже. Не придумывай
характеристики, которых нет в сообщении: незаполненные поля оставляй null.

Схема ответа (верни РОВНО такой JSON-объект):
{
  "segment": "эконом | средний | премиум | null",
  "notes": "строка или null — общий контекст запроса",
  "specs": [
    {
      "appliance": "oven | cooktop | hood | microwave | dishwasher | fridge | unknown",
      "raw_text": "исходная строка позиции из сообщения клиента",
      "width_mm": 600,            // ширина в мм или null (60 см = 600 мм)
      "color": "нержавейка | белый | чёрный | ... | null",
      "install_type": "полновстраиваемая | встраиваемая | ... | null",
      "fuel_type": "электрическая | газовая | индукционная | null",
      "burners": 3,               // число конфорок или null
      "exclude_colors": ["чёрный"], // цвета, которые клиент НЕ хочет (например 'не чёрную')
      "extra": {}                 // прочие требования ключ-значение
    }
  ]
}

Правила:
- Тип техники определяй по смыслу: "духовка/духовой шкаф" → oven;
  "варочная/панель/конфорки" → cooktop; "вытяжка" → hood;
  "СВЧ/микроволновка/микроволновая" → microwave; "посудомойка" → dishwasher;
  "холодильник" → fridge; иначе → unknown.
- Размеры в сантиметрах переводи в миллиметры (60 → 600, 45 → 450).
- "не чёрную", "не белый" и т.п. — это exclude_colors, а не color.
- "средний сегмент / средние цены" → segment="средний".
- Отвечай ТОЛЬКО JSON, без пояснений.
"""


class RequestParser:
    def __init__(self, llm: LLMProvider | None = None) -> None:
        self._llm = llm or get_llm_provider()

    def parse(self, message: str) -> CustomerRequest:
        raw = self._llm.complete_json(SYSTEM_PROMPT, message)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:  # pragma: no cover - защитный код
            raise ValueError(f"LLM вернул невалидный JSON: {raw!r}") from exc

        request = CustomerRequest.model_validate(data)
        request.original_message = message
        return request
