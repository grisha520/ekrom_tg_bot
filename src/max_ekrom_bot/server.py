"""FastAPI-сервер: принимает вебхуки МАКС и запускает подбор.

Запуск:  uvicorn max_ekrom_bot.server:app --host 0.0.0.0 --port 8080

Поток обработки входящего сообщения:
  1) валидируем секрет вебхука;
  2) достаём текст и chat_id;
  3) парсим → подбираем → (если включено подтверждение) показываем позиции;
  4) после подтверждения генерим PDF и отправляем файл клиенту.

Логика подтверждения упрощена (заглушка состояния диалога) — помечено TODO.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Header, HTTPException, Request

from .config import get_settings
from .messenger import MaxClient
from .pipeline import Pipeline

logger = logging.getLogger("max_ekrom_bot.server")

app = FastAPI(title="max-ekrom-bot")


def _format_selection(selection) -> str:
    lines = ["Подобрал по вашему запросу:\n"]
    for i, item in enumerate(selection.matched, 1):
        p = item.product
        lines.append(f"{i}. {p.title} — {p.price:.0f} ₽")
    for item in selection.unmatched:
        lines.append(f"• Не нашёл вариант под: {item.spec.raw_text or item.spec.appliance.value}")
    if selection.matched:
        lines.append(f"\nИтого: {selection.total_price:.0f} ₽")
        lines.append("\nСформировать PDF со списком? Ответьте «да».")
    return "\n".join(lines)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request, x_max_webhook_secret: str | None = Header(default=None)) -> dict:
    settings = get_settings()

    # Проверка секрета (если задан)
    if settings.max_webhook_secret and x_max_webhook_secret != settings.max_webhook_secret:
        raise HTTPException(status_code=403, detail="invalid webhook secret")

    update = await request.json()

    # TODO(api): подтвердить структуру update по dev.max.ru.
    message = (update.get("message") or {}).get("body", {}).get("text") or update.get("text")
    chat_id = (
        (update.get("message") or {}).get("recipient", {}).get("chat_id")
        or update.get("chat_id")
    )

    if not message or chat_id is None:
        return {"ok": True, "skipped": "no text/chat_id"}

    pipeline = Pipeline(settings)
    max_client = MaxClient(settings)

    try:
        result = await pipeline.run(message, generate_pdf=False)
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка обработки запроса")
        await max_client.send_text(chat_id, "Не удалось обработать запрос. Менеджер скоро ответит.")
        return {"ok": False}

    if settings.require_confirmation:
        await max_client.send_text(chat_id, _format_selection(result.selection))
        # TODO: сохранить selection в хранилище диалога по chat_id и ждать «да»,
        # затем по подтверждению вызвать pipeline.build_pdf и max_client.send_pdf.
        return {"ok": True, "stage": "awaiting_confirmation"}

    pdf_path = await pipeline.build_pdf(result.selection)
    await max_client.send_pdf(chat_id, pdf_path, caption="Ваш подбор техники")
    return {"ok": True, "stage": "sent_pdf"}
