"""Клиентский end-to-end: parse → select (enrich) → PDF для набора запросов.

Запуск (на сервере с .env и catalog.db):
  ./.venv/bin/python scripts/client_e2e_suite.py
  ./.venv/bin/python scripts/client_e2e_suite.py --html data/out/e2e_report.html
"""

from __future__ import annotations

import argparse
import asyncio
import html as html_lib
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from max_ekrom_bot.config import get_settings  # noqa: E402
from max_ekrom_bot.pdf import build_offer_pdf  # noqa: E402
from max_ekrom_bot.pipeline import Pipeline  # noqa: E402

# (запрос, ожидаемая категория/бренд — подстрока для авто-проверки)
CASES: list[tuple[str, str | None]] = [
    ("винный шкаф на 20 бутылок", "винн"),
    ("морозильная камера", "морозил"),
    ("кофемашина автоматическая", "кофе"),
    ("утюг с парогенератором", "гладил"),
    ("духовой шкаф чёрный встраиваемый", "духов"),
    ("посудомойка 45 см", "посудом"),
    ("холодильник до 60000", "холодил"),
    ("варочная не белая", "вароч"),
    ("духовка до 30000", "духов"),
    ("холодильник кроме Indesit", "холодил"),
    ("посудомойка 45 см и вытяжка 60 см", None),
    ("привет", None),
]


@dataclass
class CaseResult:
    query: str
    expect_hint: str | None
    ok: bool
    elapsed_s: float
    specs: int = 0
    matched: int = 0
    unmatched: int = 0
    categories: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    total_price: float | None = None
    pdf_bytes: int | None = None
    pdf_path: str | None = None
    error: str | None = None
    note: str | None = None


def _check(expect: str | None, categories: list[str], products: list[str], matched: int) -> tuple[bool, str | None]:
    if expect is None:
        return True, None
    blob = " ".join(categories + products).lower()
    if expect.lower() in blob:
        return True, None
    if matched == 0:
        return False, f"ничего не подобрано (ожидали «{expect}»)"
    return False, f"нет «{expect}» в {categories!r} / {products!r}"


async def run_case(pipeline: Pipeline, query: str, expect: str | None) -> CaseResult:
    t0 = time.perf_counter()
    res = CaseResult(query=query, expect_hint=expect, ok=False, elapsed_s=0.0)
    try:
        if query.strip().lower() in ("привет", "hello", "hi"):
            res.note = "edge: приветствие — в боте мягкая подсказка, пайплайн может не дать товар"
        request = pipeline.parse_message(query)
        res.specs = len(request.specs)
        selection = await pipeline.select_products(request, enrich_chosen=True)
        res.matched = len(selection.matched)
        res.unmatched = len(selection.unmatched)
        res.total_price = selection.total_price if selection.matched else None
        for item in selection.matched:
            if item.product:
                res.products.append(item.product.title[:80])
            spec = item.spec
            cat = spec.category_name or (spec.appliance.value if spec.appliance else "?")
            res.categories.append(str(cat))
        ok_cat, note = _check(expect, res.categories, res.products, res.matched)
        if selection.matched:
            pdf_path = await asyncio.to_thread(build_offer_pdf, selection, get_settings())
            res.pdf_path = str(pdf_path)
            res.pdf_bytes = pdf_path.stat().st_size
            res.ok = ok_cat and res.pdf_bytes > 5000
            if not ok_cat:
                res.note = note
            elif res.pdf_bytes <= 5000:
                res.note = "PDF слишком маленький"
        else:
            res.ok = expect is None and query.strip().lower() in ("привет", "hello", "hi")
            if not res.ok:
                res.note = note or "нет подобранных позиций"
    except Exception as exc:  # noqa: BLE001
        res.error = repr(exc)
        res.ok = False
    res.elapsed_s = round(time.perf_counter() - t0, 2)
    return res


def _html_report(results: list[CaseResult], settings) -> str:
    passed = sum(1 for r in results if r.ok)
    rows = []
    for i, r in enumerate(results, 1):
        status = "✓" if r.ok else "✗"
        cls = "ok" if r.ok else "fail"
        cats = ", ".join(r.categories) or "—"
        prods = "<br>".join(html_lib.escape(p) for p in r.products) or "—"
        note = html_lib.escape(r.note or r.error or "")
        rows.append(
            f"<tr class='{cls}'><td>{i}</td><td>{html_lib.escape(r.query)}</td>"
            f"<td>{status}</td><td>{cats}</td><td>{prods}</td>"
            f"<td>{r.matched}/{r.specs}</td><td>{r.elapsed_s}s</td>"
            f"<td>{r.pdf_bytes or '—'}</td><td>{note}</td></tr>"
        )
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>E2E клиентский подбор</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; background: #0f172a; color: #e2e8f0; }}
h1 {{ font-size: 1.4rem; }}
.meta {{ color: #94a3b8; margin-bottom: 16px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #334155; padding: 8px; vertical-align: top; }}
th {{ background: #1e293b; text-align: left; }}
tr.ok td:nth-child(3) {{ color: #22c55e; font-weight: bold; }}
tr.fail td:nth-child(3) {{ color: #ef4444; font-weight: bold; }}
.summary {{ margin: 16px 0; padding: 12px; background: #1e293b; border-radius: 8px; }}
</style></head><body>
<h1>Клиентский E2E — max-ekrom-bot</h1>
    <div class="meta">LLM: {html_lib.escape(settings.llm_provider)} / resolver: {html_lib.escape(settings.resolver_model or settings.llm_model)} · cache: {settings.use_catalog_cache}</div>
<div class="summary"><b>{passed}/{len(results)}</b> пройдено · {sum(r.elapsed_s for r in results):.1f}s суммарно</div>
<table>
<thead><tr><th>#</th><th>Запрос</th><th>OK</th><th>Категории</th><th>Товары</th><th>match/spec</th><th>Время</th><th>PDF байт</th><th>Примечание</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</body></html>"""


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", type=str, default="", help="путь к HTML-отчёту")
    parser.add_argument("--json", type=str, default="", help="путь к JSON")
    args = parser.parse_args()

    settings = get_settings()
    pipeline = Pipeline(settings)
    results: list[CaseResult] = []
    print(f"Запуск {len(CASES)} кейсов (client flow: enrich + PDF)…\n")
    for query, expect in CASES:
        print(f"  >> {query!r} …", flush=True)
        r = await run_case(pipeline, query, expect)
        results.append(r)
        mark = "OK" if r.ok else "FAIL"
        print(f"     [{mark}] {r.matched}/{r.specs} за {r.elapsed_s}s", end="")
        if r.categories:
            print(f" | {r.categories[0]}", end="")
        if r.products:
            print(f" | {r.products[0][:50]}", end="")
        print(flush=True)
        if r.note or r.error:
            print(f"     note: {r.note or r.error}")

    passed = sum(1 for r in results if r.ok)
    print(f"\nИтого: {passed}/{len(results)} OK")

    if args.json:
        Path(args.json).write_text(
            json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"JSON: {args.json}")

    html_path = args.html or str(settings.output_path / "e2e_client_report.html")
    Path(html_path).write_text(_html_report(results, settings), encoding="utf-8")
    print(f"HTML: {html_path}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
