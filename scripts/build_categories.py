"""Снять дерево категорий ekrom и сохранить снимок в data/categories.json.

Берём ЛИСТОВЫЕ категории (страницы с товарами) только из разделов «реальной
техники» (кухня, дом, уход за одеждой, климат, МБТ, красота/здоровье).
Этот файл — источник категорий для подбора (LLM выбирает раздел из него).

Запуск (нужен браузер Playwright):
    python scripts/build_categories.py
Обновлять при изменении структуры каталога на сайте.
"""

from __future__ import annotations

import asyncio
import json

from max_ekrom_bot.config import REPO_ROOT, get_settings
from max_ekrom_bot.ekrom.client import EkromClient

# Берём ВСЕ товарные разделы каталога (чёрный список — так будущие разделы
# попадут автоматически). Исключаем не-категории: дубли (дисконт, акции),
# наборы (комплекты) и служебные страницы (сравнение) — они путают резолвер и
# дублируют товары в кэше.
EXCLUDE_SECTIONS = {
    "aktsii",            # Акции (кросс-секция, дубли товаров)
    "diskont_tsentr",    # Дисконт-центр (дубли товаров со скидкой)
    "komplekt_tekhniki", # Комплекты (наборы, не единичный товар)
    "compare",           # Сравнение (инструмент, не категория)
}


def _section(href: str) -> str | None:
    parts = [p for p in href.split("/") if p]
    return parts[1] if len(parts) >= 2 and parts[0] == "catalog" else None


def _depth(href: str) -> int:
    return len([p for p in href.split("/") if p])

_JS = r"""
() => {
  const out = [];
  document.querySelectorAll('a[href*="/catalog/"]').forEach(a => {
    const href = a.getAttribute('href') || '';
    const text = (a.textContent || '').trim().replace(/\s+/g, ' ');
    if (href.indexOf('/catalog/') === 0 && text && text.length < 80) out.push([href, text]);
  });
  const m = {};
  for (const [h, t] of out) if (!(h in m)) m[h] = t;
  return m;
}
"""


async def main() -> None:
    s = get_settings()
    async with EkromClient(s) as e:
        page = e.page
        await page.goto(s.ekrom_base_url.rstrip("/") + "/catalog/",
                        wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        links: dict[str, str] = await page.evaluate(_JS)  # href -> name

    # Все категории товарных разделов (глубина ≥3: /catalog/<раздел>/<категория>/),
    # кроме корней разделов и чёрного списка. Берём все уровни (и родителя «Духовые
    # шкафы», и лист «Электрические») — LLM выберет нужную глубину под запрос.
    cats = {
        h: t for h, t in links.items()
        if (sec := _section(h)) and sec not in EXCLUDE_SECTIONS and _depth(h) >= 3
    }

    categories: list[dict] = []
    for href, name in sorted(cats.items()):
        sec = _section(href)
        section = links.get(f"/catalog/{sec}/", "") if sec else ""
        crumbs = sorted(
            ((h2, t2) for h2, t2 in cats.items() if href != h2 and href.startswith(h2)),
            key=lambda x: len(x[0]),
        )
        parts = ([section] if section else []) + [t2 for _, t2 in crumbs]
        breadcrumb = " / ".join(parts)
        categories.append({"url": href, "name": name, "path": breadcrumb})

    out_path = REPO_ROOT / "data" / "categories.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(categories, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"категорий: {len(categories)} -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
