"""End-to-end прогон через реальный пайплайн: разбор → фильтр-подбор → корзина → PDF.

Запуск:
  python scripts/test_ekrom.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from max_ekrom_bot.config import get_settings  # noqa: E402
from max_ekrom_bot.pipeline import Pipeline  # noqa: E402

EXAMPLE = """Добрый день! Можно подбор средний цен. Сегмент
1. Духовка 600 нерж, не чёрную
2. варочная, 3 конфорки. Электрическая
3. Вытяжка полновстроенная белая
4. СВЧ встроенная в пенале с духовкой"""


async def main() -> None:
    settings = get_settings()
    pipeline = Pipeline(settings)

    request = pipeline.parse_message(EXAMPLE)
    print(f"Разобрано позиций: {len(request.specs)} | сегмент: {request.segment}")
    for s in request.specs:
        print(f"   {s.appliance.value:10s} color={s.color} excl={s.exclude_colors} "
              f"fuel={s.fuel_type} width={s.width_mm} burners={s.burners}")

    print("\n>> Подбор по категориям с фильтрами...")
    selection = await pipeline.select_products(request)
    print("\n--- ПОДБОР ---")
    for item in selection.matched:
        print(f"  ✓ {item.product.title[:55]:55s} {item.product.price:>10.0f} ₽")
        print(f"      {item.reason}")
    for item in selection.unmatched:
        print(f"  ✗ нет варианта: {item.spec.raw_text!r}")
    print(f"  ИТОГО: {selection.total_price:.0f} ₽")

    if not selection.matched:
        print("\nНет подобранных товаров — PDF не формируем.")
        return

    print("\n>> Корзина + PDF...")
    try:
        path = await pipeline.build_pdf(selection)
        print(f"   PDF сохранён: {path} ({path.stat().st_size} байт)")
    except Exception as exc:  # noqa: BLE001
        print(f"   Ошибка: {exc!r}")

    print("\nГотово.")


if __name__ == "__main__":
    asyncio.run(main())
