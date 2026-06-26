"""Замер подбора (премиум, 4 позиции) — для проверки скорости/надёжности."""

import asyncio
import time

from max_ekrom_bot.pipeline import Pipeline

MSG = """Подберите премиум технику:
- духовой шкаф чёрный 60
- индукционная варочная панель 4 конфорки графит
- вытяжка полновстраиваемая чёрная
- посудомойка встраиваемая 60"""


async def main():
    p = Pipeline()
    req = p.parse_message(MSG)
    t = time.time()
    sel = await p.select_products(req)
    print(f"подбор: {time.time() - t:.1f}s")
    for it in sel.items:
        if it.product:
            print(f"  + {it.spec.appliance.value}: {it.product.title[:46]}")
        else:
            print(f"  - {it.spec.appliance.value}: НЕ НАЙДЕНО — {it.reason[:40]}")


asyncio.run(main())
