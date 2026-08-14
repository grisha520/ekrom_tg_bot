"""Прогнать сообщения клиентов через бота и выдать подборы в машинном виде.

Нужен для сверки с эталонными подборками менеджера: на вход — список
формулировок, на выход — JSON с тем, что собрал бот (бренд, позиции, цены,
акция, итог, пометки о заменах и позиции, ушедшие менеджеру).

Запуск на сервере:
  ./.venv/bin/python scripts/compare_picks.py messages.txt --json picks.json

`messages.txt` — по одному сообщению клиента на строку (пустые строки и
строки, начинающиеся с #, игнорируются). Без аргумента читает stdin.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from max_ekrom_bot.config import get_settings  # noqa: E402
from max_ekrom_bot.pipeline import Pipeline  # noqa: E402


async def run_one(pipeline: Pipeline, message: str) -> dict:
    """Собрать комплект по сообщению и вернуть плоский разбор для сверки."""
    out: dict = {"message": message}
    try:
        request = pipeline.parse_message(message)
        out["specs"] = [
            {"позиция": s.raw_text, "тип": s.appliance.value, "цвет": s.color,
             "бренд": s.brand, "модель": s.model, "бюджет": s.max_price,
             "ширина": s.width_mm}
            for s in request.specs
        ]
        kit = await pipeline.select_kit(request, enrich=False)
        if kit is None:
            out["итог"] = "комплект не собран"
            return out

        c = kit.combo
        out.update(
            бренд=kit.brand,
            покрытие=f"{kit.coverage}/{kit.requested}",
            позиции=[
                {"товар": m.product.title, "цена": m.product.price,
                 "url": m.product.url, "пометка": m.reason or None}
                for m in kit.selection.matched
            ],
            не_закрыто=[m.spec.raw_text for m in kit.selection.unmatched],
            менеджеру=[s.raw_text for s in (kit.manager_specs or [])],
            акция=c.campaign_title or c.campaign_slug,
            механика=c.mechanic,
            сумма=c.subtotal,
            выгода=c.discount_total,
            итог=c.final_total,
            подарок=c.gift_title,
            уточнить_у_менеджера=c.manager_review,
            дизайн=kit.design_note,
            другие_акции=[{"акция": t, "выгода": v} for t, v in (c.alternatives or [])],
            другие_бренды=[
                {"бренд": r.brand, "покрытие": r.coverage, "выгода": r.benefit}
                for r in (kit.rivals or [])
            ],
        )
    except Exception as exc:  # noqa: BLE001 — один кейс не валит прогон
        out["ошибка"] = repr(exc)[:300]
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="файл с сообщениями (по одному на строку)")
    ap.add_argument("--json", default="", help="куда сложить результат")
    args = ap.parse_args()

    raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    messages = [ln.strip() for ln in raw.splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
    if not messages:
        print("нет сообщений на входе")
        return 1

    pipeline = Pipeline(get_settings())
    results = []
    for i, msg in enumerate(messages, 1):
        print(f"[{i}/{len(messages)}] {msg[:70]}…", flush=True)
        r = await run_one(pipeline, msg)
        results.append(r)
        if "ошибка" in r:
            print(f"    ОШИБКА: {r['ошибка']}", flush=True)
        else:
            print(f"    {r.get('бренд', '—')} {r.get('покрытие', '')} "
                  f"акция={r.get('акция')} выгода={r.get('выгода')} "
                  f"итог={r.get('итог')}", flush=True)

    payload = json.dumps(results, ensure_ascii=False, indent=2)
    if args.json:
        Path(args.json).write_text(payload, encoding="utf-8")
        print(f"\nJSON: {args.json}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
