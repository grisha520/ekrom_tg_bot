"""Комплектный end-to-end: parse → select_kit → format_kit → PDF (боевой поток).

Повторяет путь клиента в Telegram-боте (KIT_MODE) на реальных формулировках:
LLM-разбор запроса, сборка комплекта одного бренда по акциям, клиентское
сообщение и PDF. Для каждого кейса — автопроверки покрытия, бренда, честности
расчёта (итог сходится с выгодой, у применённой акции есть срок) и размера PDF.

Запуск (на сервере с .env и catalog.db):
  ./.venv/bin/python scripts/kit_e2e_suite.py
  ./.venv/bin/python scripts/kit_e2e_suite.py --html data/out/kit_e2e.html
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
from max_ekrom_bot.messenger.client_ux import format_kit  # noqa: E402
from max_ekrom_bot.pipeline import Pipeline  # noqa: E402

TARGET_BRANDS = ("Korting", "Maunfeld", "Midea", "Lex", "Kuppersberg")


@dataclass
class KitCase:
    """Кейс прогона: формулировка клиента + что обязаны получить на выходе."""

    query: str
    min_cover: int = 1                       # минимум закрытых позиций
    brands: tuple[str, ...] | None = None    # допустимые бренды комплекта (или любые из 5)
    expect_manager_specs: bool = False       # должны быть позиции «менеджеру» (вне охвата)
    allow_empty: bool = False                # расплывчатый запрос: допустимо без комплекта


# Реальные формулировки клиентов (из примеров онбординга, эталонных подборок
# и типовых обращений). Порядок — от базовых к каверзным.
CASES: list[KitCase] = [
    KitCase("духовой шкаф, варочная панель и вытяжка", min_cover=3),
    KitCase("холодильник, посудомойка, духовка и варочная", min_cover=4),
    KitCase("можно Kuppersberg Maunfeld: духовой шкаф, варочная панель, вытяжка, "
            "посудомоечная машина", min_cover=4, brands=("Kuppersberg", "Maunfeld")),
    KitCase("комплект: духовка белая, варочная панель чёрная и вытяжка", min_cover=3),
    KitCase("духовой шкаф и варочная панель Korting", min_cover=2, brands=("Korting",)),
    KitCase("духовка, варочная, вытяжка, посудомойка 45 см и холодильник", min_cover=5),
    KitCase("варочная панель домино и духовой шкаф", min_cover=2),
    KitCase("духовка, варочная, вытяжка и мойка со смесителем", min_cover=3,
            expect_manager_specs=True),
    KitCase("стиральная машина и сушильная машина Midea", min_cover=1),
    KitCase("духовка до 30000 и варочная до 20000", min_cover=2),
    KitCase("микроволновка, духовка и варочная", min_cover=3),
    KitCase("хочу технику для кухни", allow_empty=True),
]


@dataclass
class CaseResult:
    query: str
    ok: bool
    elapsed_s: float
    specs: int = 0
    coverage: int = 0
    brand: str | None = None
    campaign: str | None = None
    mechanic: str | None = None
    subtotal: float | None = None
    benefit: float | None = None
    final: float | None = None
    gift: str | None = None
    manager_review: bool = False
    manager_specs: int = 0
    products: list[str] = field(default_factory=list)
    message_len: int = 0
    pdf_bytes: int | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)


def _honesty_notes(combo) -> list[str]:
    """Инварианты расчёта: применённая акция обязана сходиться в рублях."""
    notes: list[str] = []
    if combo.applied:
        if combo.discount_total <= 0:
            notes.append("акция применена, но выгода ≤ 0")
        deducted = abs(combo.final_total - (combo.subtotal - combo.discount_total)) < 0.01
        on_top = bool(combo.gift_title) and abs(combo.final_total - combo.subtotal) < 0.01
        if not (deducted or on_top):
            notes.append("итог не сходится: ни вычет, ни подарок-сверху")
        if not combo.campaign_slug:
            notes.append("применённая акция без slug")
    else:
        if abs(combo.final_total - combo.subtotal) > 0.01:
            notes.append("акция не применена, но итог изменён")
    return notes


async def run_case(pipeline: Pipeline, case: KitCase) -> CaseResult:
    t0 = time.perf_counter()
    res = CaseResult(query=case.query, ok=False, elapsed_s=0.0)
    try:
        request = pipeline.parse_message(case.query)
        res.specs = len(request.specs)
        kit = await asyncio.wait_for(pipeline.select_kit(request), timeout=240)

        if kit is None or kit.coverage == 0:
            res.ok = case.allow_empty
            if not res.ok:
                res.notes.append("комплект не собран")
            res.elapsed_s = round(time.perf_counter() - t0, 2)
            return res

        res.coverage = kit.coverage
        res.brand = kit.brand
        res.manager_specs = len(kit.manager_specs or [])
        combo = kit.combo
        res.campaign = combo.campaign_slug
        res.mechanic = combo.mechanic
        res.subtotal, res.benefit, res.final = combo.subtotal, combo.discount_total, combo.final_total
        res.gift = combo.gift_title
        res.manager_review = combo.manager_review
        res.products = [m.product.title[:80] for m in kit.selection.matched if m.product]

        # Клиентское сообщение должно рендериться (это то, что видит человек).
        msg = format_kit(kit, get_settings())
        res.message_len = len(msg)

        # PDF — боевой путь: HTML→Chromium, фолбэк reportlab (как в telegram_app).
        try:
            pdf_path = await pipeline.build_kit_pdf_html(kit, None)
        except Exception:  # noqa: BLE001 — фолбэк как в боте
            pdf_path = await asyncio.to_thread(pipeline.build_kit_pdf, kit, None)
        res.pdf_bytes = pdf_path.stat().st_size

        # ── Автопроверки ──
        if res.coverage < case.min_cover:
            res.notes.append(f"покрытие {res.coverage} < {case.min_cover}")
        if kit.brand not in TARGET_BRANDS:
            res.notes.append(f"бренд вне пятёрки: {kit.brand!r}")
        if case.brands and kit.brand not in case.brands:
            res.notes.append(f"бренд {kit.brand} вне разрешённых {case.brands}")
        if case.expect_manager_specs and not res.manager_specs:
            res.notes.append("ожидали позиции для менеджера — их нет")
        if res.message_len < 100:
            res.notes.append("подозрительно короткое сообщение клиенту")
        if (res.pdf_bytes or 0) <= 5000:
            res.notes.append("PDF слишком маленький")
        res.notes += _honesty_notes(combo)
        res.ok = not res.notes
    except Exception as exc:  # noqa: BLE001
        res.error = repr(exc)[:300]
    res.elapsed_s = round(time.perf_counter() - t0, 2)
    return res


def _html_report(results: list[CaseResult], settings) -> str:
    passed = sum(1 for r in results if r.ok)
    rows = []
    for i, r in enumerate(results, 1):
        cls = "ok" if r.ok else "fail"
        prods = "<br>".join(html_lib.escape(p) for p in r.products) or "—"
        money = (f"{r.subtotal:,.0f} − {r.benefit:,.0f} = {r.final:,.0f}".replace(",", " ")
                 if r.subtotal is not None else "—")
        note = html_lib.escape("; ".join(r.notes) or r.error or "")
        rows.append(
            f"<tr class='{cls}'><td>{i}</td><td>{html_lib.escape(r.query)}</td>"
            f"<td>{'✓' if r.ok else '✗'}</td><td>{r.brand or '—'}</td>"
            f"<td>{r.coverage}/{r.specs}</td>"
            f"<td>{html_lib.escape(r.campaign or '—')}<br>{r.mechanic or ''}</td>"
            f"<td>{money}</td><td>{html_lib.escape(r.gift or '—')}</td>"
            f"<td>{r.pdf_bytes or '—'}</td><td>{r.elapsed_s}s</td><td>{note}</td></tr>"
        )
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>E2E комплекты — max-ekrom-bot</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; background: #0f172a; color: #e2e8f0; }}
h1 {{ font-size: 1.4rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #334155; padding: 8px; vertical-align: top; }}
th {{ background: #1e293b; text-align: left; }}
tr.ok td:nth-child(3) {{ color: #22c55e; font-weight: bold; }}
tr.fail td:nth-child(3) {{ color: #ef4444; font-weight: bold; }}
.summary {{ margin: 16px 0; padding: 12px; background: #1e293b; border-radius: 8px; }}
</style></head><body>
<h1>E2E комплектов (5 брендов по акциям)</h1>
<div class="summary"><b>{passed}/{len(results)}</b> пройдено ·
{sum(r.elapsed_s for r in results):.0f}s суммарно ·
LLM {html_lib.escape(settings.llm_provider)}</div>
<table>
<thead><tr><th>#</th><th>Запрос</th><th>OK</th><th>Бренд</th><th>Покрытие</th>
<th>Акция</th><th>Сумма − выгода = итог</th><th>Подарок</th><th>PDF</th>
<th>Время</th><th>Примечание</th></tr></thead>
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
    print(f"Запуск {len(CASES)} комплектных кейсов (боевой поток)…\n", flush=True)
    for case in CASES:
        print(f"  >> {case.query!r} …", flush=True)
        r = await run_case(pipeline, case)
        results.append(r)
        mark = "OK" if r.ok else "FAIL"
        line = f"     [{mark}] {r.brand or '—'} {r.coverage}/{r.specs} за {r.elapsed_s}s"
        if r.campaign:
            line += f" | {r.campaign} выгода {r.benefit:,.0f}".replace(",", " ")
        print(line, flush=True)
        if r.notes or r.error:
            print(f"     note: {'; '.join(r.notes) or r.error}", flush=True)

    passed = sum(1 for r in results if r.ok)
    print(f"\nИтого: {passed}/{len(results)} OK", flush=True)

    if args.json:
        Path(args.json).write_text(
            json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    html_path = args.html or str(settings.output_path / "kit_e2e_report.html")
    Path(html_path).write_text(_html_report(results, settings), encoding="utf-8")
    print(f"HTML: {html_path}", flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
