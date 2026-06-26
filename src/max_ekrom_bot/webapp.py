"""Веб-интерфейс для менеджера: подбор техники и формирование PDF.

Поток:
  1. Менеджер вставляет текст запроса клиента и жмёт «Подобрать».
  2. Сервер парсит запрос и подбирает товары на ekrom (это занимает ~минуту —
     идёт автоматизация браузера). Показывается превью позиций.
  3. Менеджер жмёт «Сформировать PDF» → товары кладутся в корзину, PDF
     скачивается и отдаётся на загрузку. Дальше менеджер отправляет файл
     клиенту вручную.

Запуск:
  uvicorn max_ekrom_bot.webapp:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import html
import uuid
from dataclasses import dataclass

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import ValidationError

from .config import get_settings
from .ekrom.client import EkromAuthError, EkromCartError
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
.warn { color:#f59e0b; }
.badge { display:inline-block; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.03em; color:#0f172a; background:#f59e0b; border-radius:6px; padding:2px 7px; margin-bottom:5px; }
.note { color:var(--muted); font-size:13px; margin-top:10px; }
a.btnlink { display:inline-block; text-decoration:none; }
.row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
button:disabled { opacity:.55; cursor:default; filter:none; }
.overlay { position:fixed; inset:0; background:rgba(15,23,42,.88); display:none; align-items:center; justify-content:center; z-index:50; backdrop-filter: blur(2px); }
.overlay.show { display:flex; }
.spinbox { background:var(--card); border:1px solid #334155; border-radius:16px; padding:30px 34px; text-align:center; max-width:380px; width:calc(100% - 40px); }
.spinner { width:44px; height:44px; border:4px solid #334155; border-top-color:var(--accent); border-radius:50%; margin:0 auto 18px; animation:spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.spinbox .stage { font-size:15px; font-weight:600; margin:0 0 6px; }
.spinbox .elapsed { color:var(--muted); font-size:13px; font-variant-numeric: tabular-nums; min-height:18px; }
.spinbox .hint { color:var(--muted); font-size:12px; margin-top:12px; }
"""


_PDF_OVERLAY = """
<div id="pdf-overlay" class="overlay" role="alert" aria-live="assertive">
  <div class="spinbox">
    <div class="spinner" id="pdf-spinner"></div>
    <p class="stage" id="pdf-stage">Готовим корзину ekrom…</p>
    <p class="elapsed" id="pdf-elapsed"></p>
    <p class="hint">Идёт автоматизация сайта ekrom — это может занять до минуты при медленном ответе сайта. Не закрывайте вкладку.</p>
  </div>
</div>"""

_PDF_SCRIPT = """
<script>
(function () {
  var form = document.getElementById('pdf-form');
  if (!form) return;
  var overlay = document.getElementById('pdf-overlay');
  var stageEl = document.getElementById('pdf-stage');
  var elapsedEl = document.getElementById('pdf-elapsed');
  var spinner = document.getElementById('pdf-spinner');
  var btn = document.getElementById('pdf-btn');
  var timer = null, started = 0;
  var stages = [
    [0, 'Открываем корзину ekrom…'],
    [6, 'Добавляем товары в корзину…'],
    [16, 'Формируем PDF…'],
    [30, 'Сайт ekrom отвечает медленно — ждём ответа…']
  ];
  function tick() {
    var s = Math.floor((Date.now() - started) / 1000);
    elapsedEl.textContent = 'прошло ' + s + ' с';
    var msg = stages[0][1];
    for (var i = 0; i < stages.length; i++) { if (s >= stages[i][0]) msg = stages[i][1]; }
    stageEl.textContent = msg;
  }
  function stop() { if (timer) { clearInterval(timer); timer = null; } }
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (btn) btn.disabled = true;
    overlay.classList.add('show');
    started = Date.now(); tick(); timer = setInterval(tick, 500);
    fetch(form.action, { method: 'POST', body: new FormData(form) })
      .then(function (resp) {
        var ct = resp.headers.get('content-type') || '';
        if (resp.ok && ct.indexOf('application/pdf') !== -1) {
          var name = 'podbor.pdf';
          var cd = resp.headers.get('content-disposition') || '';
          var m = /filename\\*?=(?:UTF-8'')?\"?([^\";]+)/i.exec(cd);
          if (m) { try { name = decodeURIComponent(m[1]); } catch (err) { name = m[1]; } }
          return resp.blob().then(function (blob) {
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url; a.download = name;
            document.body.appendChild(a); a.click(); a.remove();
            URL.revokeObjectURL(url);
            stop();
            spinner.style.display = 'none';
            stageEl.textContent = 'Готово — файл скачивается.';
            elapsedEl.textContent = '';
            setTimeout(function () {
              overlay.classList.remove('show');
              spinner.style.display = '';
              if (btn) btn.disabled = false;
            }, 2000);
          });
        }
        return resp.text().then(function (t) {
          stop();
          document.open(); document.write(t); document.close();
        });
      })
      .catch(function () {
        stop();
        spinner.style.display = 'none';
        stageEl.textContent = 'Ошибка сети — попробуйте ещё раз.';
        elapsedEl.textContent = '';
        setTimeout(function () {
          overlay.classList.remove('show');
          spinner.style.display = '';
          if (btn) btn.disabled = false;
        }, 2800);
      });
  });
})();
</script>"""


def _layout(body: str) -> str:
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Подбор техники ekrom</title><style>{_PAGE_CSS}</style></head>
<body><div class="wrap">{body}</div></body></html>"""


def _home_form(message: str = "") -> str:
    return _layout(f"""
<h1>Подбор техники ekrom</h1>
<p class="sub">Вставьте сообщение клиента — система подберёт товары и сформирует PDF.</p>
<div id="sess" style="display:inline-flex;align-items:center;gap:7px;font-size:13px;color:var(--muted);margin:0 0 18px;padding:5px 11px;border:1px solid #334155;border-radius:999px;">
  <span id="sess-dot" style="width:9px;height:9px;border-radius:50%;background:var(--muted);"></span>
  <span id="sess-text">проверяю сессию ekrom…</span>
</div>
<form method="post" action="/match" class="card">
  <textarea name="message" placeholder="Например:&#10;1. Духовка 600 нерж, не чёрную&#10;2. варочная, 3 конфорки. Электрическая&#10;3. Вытяжка полновстроенная белая&#10;4. СВЧ встроенная">{html.escape(message)}</textarea>
  <div class="row" style="margin-top:14px;">
    <button type="submit">Подобрать</button>
    <span class="note">Подбор идёт через сайт ekrom и занимает ~минуту.</span>
  </div>
</form>
<form method="post" action="/login" class="row" style="margin-top:4px;">
  <button type="submit" style="background:#334155;">Обновить сессию ekrom</button>
  <span class="note">Проверит вход в кабинет ekrom и при необходимости залогинится заново.</span>
</form>
<script>
fetch('/session/status').then(r => r.json()).then(d => {{
  var dot = document.getElementById('sess-dot');
  var txt = document.getElementById('sess-text');
  if (d.authenticated) {{ dot.style.background = 'var(--ok)'; txt.textContent = 'вход активен'; }}
  else {{ dot.style.background = 'var(--muted)'; txt.textContent = 'вход не выполнен'; }}
}}).catch(function() {{
  document.getElementById('sess-text').textContent = 'статус сессии неизвестен';
}});
</script>""")


def _fmt_price(value: float | None) -> str:
    if not value:
        return "—"
    return f"{int(round(value)):,}".replace(",", " ") + " ₽"


def _preview(token: str, selection: SelectionResult) -> str:
    rows = []
    for item in selection.matched:
        p = item.product
        is_sub = "Предложена замена" in item.reason
        badge = '<div class="badge">замена</div>' if is_sub else ""
        reason_cls = "reason warn" if is_sub else "reason"
        rows.append(f"""<div class="item"><div>
          {badge}
          <div class="title">{html.escape(p.title)}</div>
          <div class="{reason_cls}">{html.escape(item.reason)}</div>
        </div><div class="price">{_fmt_price(p.price)}</div></div>""")
    for item in selection.unmatched:
        reason = f'<div class="reason">{html.escape(item.reason)}</div>' if item.reason else ""
        rows.append(f"""<div class="item"><div>
          <div class="title bad">Не найдено: {html.escape(item.spec.raw_text or item.spec.appliance.value)}</div>
          {reason}
        </div><div class="price">—</div></div>""")

    total = _fmt_price(selection.total_price)
    pdf_btn = ""
    if selection.matched:
        pdf_btn = f"""<form id="pdf-form" method="post" action="/pdf" class="row" style="margin-top:18px;">
          <input type="hidden" name="token" value="{token}">
          <button id="pdf-btn" type="submit">Сформировать PDF</button>
          <span class="note">Товары добавятся в корзину ekrom и PDF скачается.</span>
        </form>{_PDF_OVERLAY}{_PDF_SCRIPT}"""

    return _layout(f"""
<h1>Подобранные позиции</h1>
<p class="sub"><a href="/" style="color:var(--muted)">← новый запрос</a></p>
<div class="card">
  {''.join(rows)}
  <div class="total"><span>Итого</span><span>{total}</span></div>
</div>
{pdf_btn}""")


def _notice(
    title: str,
    text: str,
    kind: str = "bad",
    hint: str = "После повторного входа вернитесь и снова нажмите «Подобрать».",
) -> str:
    hint_html = (
        f'<p class="note" style="margin:0;">{html.escape(hint)}</p>' if hint else ""
    )
    return _layout(f"""
<h1>{html.escape(title)}</h1>
<div class="card">
  <p class="{kind}" style="font-size:15px;margin:0 0 8px;">{html.escape(text)}</p>
  {hint_html}
</div>
<p class="sub" style="margin-top:16px;"><a href="/" style="color:var(--muted)">← на главную</a></p>""")


@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    return _home_form()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/match", response_class=HTMLResponse)
async def match(message: str = Form(...)) -> Response:
    message = message.strip()
    if not message:
        return _home_form()

    pipeline = Pipeline(get_settings())

    # Разбор сообщения моделью: ловим кривой JSON / несоответствие схеме
    # и недоступность сервиса, чтобы не падать 500.
    try:
        request = pipeline.parse_message(message)
    except (ValueError, ValidationError):
        return HTMLResponse(
            _notice(
                "Не удалось разобрать сообщение",
                "Модель вернула некорректный ответ на этот текст. Попробуйте "
                "переформулировать запрос — например, оформить позиции "
                "нумерованным списком — и нажмите «Подобрать» ещё раз.",
            ),
            status_code=502,
        )
    except Exception:  # noqa: BLE001 — сеть/ключ/таймаут провайдера
        return HTMLResponse(
            _notice(
                "Сервис разбора недоступен",
                "Не удалось обратиться к модели Groq. Проверьте интернет-соединение "
                "и ключ GROQ_API_KEY в .env, затем повторите попытку.",
            ),
            status_code=503,
        )

    selection = await pipeline.select_products(request)

    token = uuid.uuid4().hex
    _STORE[token] = _Stored(selection=selection, message=message)
    return _preview(token, selection)


@app.get("/session/status")
async def session_status() -> dict:
    pipeline = Pipeline(get_settings())
    try:
        ok = await pipeline.is_ekrom_authenticated()
    except Exception:  # noqa: BLE001 — статус не критичен, при сбое считаем «не выполнен»
        ok = False
    return {"authenticated": ok}


@app.post("/login")
async def login() -> Response:
    pipeline = Pipeline(get_settings())
    try:
        already = await pipeline.refresh_ekrom_session()
    except ValueError:
        return HTMLResponse(
            _notice(
                "Учётные данные не заданы",
                "В .env не заполнены EKROM_LOGIN и EKROM_PASSWORD — автоматический "
                "вход невозможен.",
                hint="",
            ),
            status_code=400,
        )
    except EkromAuthError as exc:
        return HTMLResponse(
            _notice("Не удалось войти", str(exc), hint=""),
            status_code=502,
        )
    except Exception:  # noqa: BLE001 — сеть/браузер
        return HTMLResponse(
            _notice(
                "Ошибка входа",
                "Не удалось обновить сессию ekrom (проблема с сетью или браузером). "
                "Попробуйте ещё раз.",
                hint="",
            ),
            status_code=502,
        )

    if already:
        return HTMLResponse(
            _notice(
                "Вход уже выполнен",
                "Сессия ekrom активна — повторный вход не требуется.",
                kind="ok",
                hint="",
            )
        )
    return HTMLResponse(
        _notice(
            "Вход выполнен",
            "Сессия ekrom обновлена. Теперь можно подбирать товары и формировать PDF.",
            kind="ok",
            hint="",
        )
    )


@app.post("/pdf")
async def pdf(token: str = Form(...)) -> Response:
    stored = _STORE.get(token)
    if not stored:
        raise HTTPException(status_code=404, detail="Подбор не найден — начните заново")

    pipeline = Pipeline(get_settings())
    try:
        path = await pipeline.build_pdf(stored.selection)
    except EkromCartError as exc:
        items = "; ".join(exc.missing_titles)
        return HTMLResponse(
            _notice(
                "Не удалось добавить часть товаров",
                "В корзину ekrom не удалось добавить: "
                f"{items}. PDF не сформирован — повторите попытку или "
                "уберите эти позиции из подбора.",
                hint="",
            ),
            status_code=409,
        )
    except EkromAuthError:
        return HTMLResponse(
            _notice(
                "Сессия ekrom истекла",
                "Не удалось открыть корзину: вход в кабинет ekrom больше не активен. "
                "Выполните повторный вход командой «python scripts/login_ekrom.py» "
                "и попробуйте снова.",
            ),
            status_code=409,
        )
    except PlaywrightTimeoutError:
        return HTMLResponse(
            _notice(
                "Не удалось сформировать PDF",
                "Сайт ekrom не отдал файл вовремя (возможна медленная загрузка или "
                "истёкшая сессия). Проверьте вход (python scripts/login_ekrom.py) и "
                "повторите попытку.",
            ),
            status_code=504,
        )
    return FileResponse(path, media_type="application/pdf", filename=path.name)
