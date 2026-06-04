"""Веб-интерфейс для менеджера (полуавтомат вместо бота МАКС).

Поток:
  1. Менеджер вставляет текст запроса клиента и жмёт «Подобрать».
  2. Сервер парсит запрос и подбирает товары на ekrom (это занимает ~минуту —
     идёт автоматизация браузера). Показывается превью позиций.
  3. Менеджер жмёт «Сформировать PDF» → товары кладутся в корзину, PDF
     скачивается и отдаётся на загрузку. Дальше менеджер отправляет файл
     клиенту в МАКС вручную.

Запуск:
  uvicorn max_ekrom_bot.webapp:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import html
import uuid
from dataclasses import dataclass

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from .config import get_settings
from .models import SelectionResult
from .pipeline import Pipeline

app = FastAPI(title="max-ekrom-bot — подбор для менеджера")


@dataclass
class _Stored:
    selection: SelectionResult
    message: str


# Простое хранилище подборов между шагами (локальный инструмент на одного менеджера).
_STORE: dict[str, _Stored] = {}

_PAGE_CSS = """
:root { --bg:#0f172a; --card:#1e293b; --accent:#2563eb; --text:#e2e8f0; --muted:#94a3b8; --ok:#22c55e; --bad:#ef4444; }
* { box-sizing: border-box; }
body { margin:0; font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:var(--bg); color:var(--text); }
.wrap { max-width: 820px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 24px; font-size: 14px; }
.card { background: var(--card); border-radius: 14px; padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 0 rgba(255,255,255,.04); }
textarea { width:100%; min-height: 160px; background:#0b1220; color:var(--text); border:1px solid #334155; border-radius:10px; padding:12px; font-size:15px; resize:vertical; }
button { background:var(--accent); color:#fff; border:0; border-radius:10px; padding:12px 20px; font-size:15px; cursor:pointer; }
button:hover { filter: brightness(1.08); }
.item { display:flex; justify-content:space-between; gap:12px; padding:12px 0; border-bottom:1px solid #334155; }
.item:last-child { border-bottom:0; }
.title { font-weight:600; }
.reason { color:var(--muted); font-size:13px; margin-top:4px; }
.price { white-space:nowrap; font-variant-numeric: tabular-nums; }
.total { display:flex; justify-content:space-between; font-size:18px; font-weight:700; padding-top:14px; }
.bad { color:var(--bad); }
.ok { color:var(--ok); }
.note { color:var(--muted); font-size:13px; margin-top:10px; }
a.btnlink { display:inline-block; text-decoration:none; }
.row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
"""


def _layout(body: str) -> str:
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Подбор техники ekrom</title><style>{_PAGE_CSS}</style></head>
<body><div class="wrap">{body}</div></body></html>"""


def _home_form(message: str = "") -> str:
    return _layout(f"""
<h1>Подбор техники ekrom</h1>
<p class="sub">Вставьте сообщение клиента — система подберёт товары и сформирует PDF.</p>
<form method="post" action="/match" class="card">
  <textarea name="message" placeholder="Например:&#10;1. Духовка 600 нерж, не чёрную&#10;2. варочная, 3 конфорки. Электрическая&#10;3. Вытяжка полновстроенная белая&#10;4. СВЧ встроенная">{html.escape(message)}</textarea>
  <div class="row" style="margin-top:14px;">
    <button type="submit">Подобрать</button>
    <span class="note">Подбор идёт через сайт ekrom и занимает ~минуту.</span>
  </div>
</form>""")


def _fmt_price(value: float | None) -> str:
    if not value:
        return "—"
    return f"{int(round(value)):,}".replace(",", " ") + " ₽"


def _preview(token: str, selection: SelectionResult) -> str:
    rows = []
    for item in selection.matched:
        p = item.product
        rows.append(f"""<div class="item"><div>
          <div class="title">{html.escape(p.title)}</div>
          <div class="reason">{html.escape(item.reason)}</div>
        </div><div class="price">{_fmt_price(p.price)}</div></div>""")
    for item in selection.unmatched:
        rows.append(f"""<div class="item"><div>
          <div class="title bad">Не найдено: {html.escape(item.spec.raw_text or item.spec.appliance.value)}</div>
        </div><div class="price">—</div></div>""")

    total = _fmt_price(selection.total_price)
    pdf_btn = ""
    if selection.matched:
        pdf_btn = f"""<form method="post" action="/pdf" class="row" style="margin-top:18px;">
          <input type="hidden" name="token" value="{token}">
          <button type="submit">Сформировать PDF</button>
          <span class="note">Товары добавятся в корзину ekrom и PDF скачается.</span>
        </form>"""

    return _layout(f"""
<h1>Подобранные позиции</h1>
<p class="sub"><a href="/" style="color:var(--muted)">← новый запрос</a></p>
<div class="card">
  {''.join(rows)}
  <div class="total"><span>Итого</span><span>{total}</span></div>
</div>
{pdf_btn}""")


@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    return _home_form()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/match", response_class=HTMLResponse)
async def match(message: str = Form(...)) -> str:
    message = message.strip()
    if not message:
        return _home_form()

    pipeline = Pipeline(get_settings())
    request = pipeline.parse_message(message)
    selection = await pipeline.select_products(request)

    token = uuid.uuid4().hex
    _STORE[token] = _Stored(selection=selection, message=message)
    return _preview(token, selection)


@app.post("/pdf")
async def pdf(token: str = Form(...)) -> FileResponse:
    stored = _STORE.get(token)
    if not stored:
        raise HTTPException(status_code=404, detail="Подбор не найден — начните заново")

    pipeline = Pipeline(get_settings())
    path = await pipeline.build_pdf(stored.selection)
    return FileResponse(path, media_type="application/pdf", filename=path.name)
