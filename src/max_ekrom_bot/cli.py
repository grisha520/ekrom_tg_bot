"""CLI для запуска ядра без мессенджера — удобно для отладки и ручного режима.

Примеры:
  python -m max_ekrom_bot.cli parse "Духовка 600 нерж ..."
  python -m max_ekrom_bot.cli select "Духовка 600 нерж ..."
  python -m max_ekrom_bot.cli run "Духовка 600 нерж ..." --pdf
"""

from __future__ import annotations

import asyncio

import typer

from .pipeline import Pipeline

app = typer.Typer(help="max-ekrom-bot — подбор техники на ekrom.ru по тексту запроса")


@app.command()
def parse(message: str) -> None:
    """Разобрать сообщение клиента в структурированный запрос (LLM)."""
    pipeline = Pipeline()
    request = pipeline.parse_message(message)
    typer.echo(request.model_dump_json(indent=2))


@app.command()
def select(message: str) -> None:
    """Разобрать + найти/подобрать товары (выводит позиции, без PDF)."""
    pipeline = Pipeline()
    request = pipeline.parse_message(message)
    selection = asyncio.run(pipeline.select_products(request))
    for i, item in enumerate(selection.matched, 1):
        typer.echo(f"{i}. {item.product.title} — {item.product.price} ₽  | {item.reason}")
    for item in selection.unmatched:
        typer.echo(f"[нет варианта] {item.spec.raw_text or item.spec.appliance.value}")
    typer.echo(f"Итого: {selection.total_price:.0f} ₽")


@app.command()
def run(message: str, pdf: bool = typer.Option(False, help="Сгенерировать PDF (добавит товары в корзину)")) -> None:
    """Полный проход: разбор → подбор → (опц.) корзина+PDF."""
    pipeline = Pipeline()

    async def _run():
        request = pipeline.parse_message(message)
        selection = await pipeline.select_products(request)
        path = await pipeline.build_pdf(selection) if pdf else None
        return selection, path

    selection, path = asyncio.run(_run())
    for i, item in enumerate(selection.matched, 1):
        typer.echo(f"{i}. {item.product.title} — {item.product.price} ₽")
    if path:
        typer.echo(f"PDF сохранён: {path}")


if __name__ == "__main__":
    app()
