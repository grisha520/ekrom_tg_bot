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

# Разделы реальной техники + сантехника для кухни (мойки, смесители — их часто
# спрашивают клиенты). Без аксессуаров/акций/дисконта/сравнения/комплектов.
INCLUDE_PREFIXES = (
    "/catalog/tekhnika_dlya_kukhni/",
    "/catalog/santekhnika_dlya_kukhni/",
    "/catalog/tekhnika_dlya_doma/",
    "/catalog/tekhnika_dlya_ukhoda_za_odezhdoy/",
    "/catalog/klimaticheskaya_tekhnika/",
    "/catalog/mbt/",
    "/catalog/krasota_i_zdorove/",
)

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

    # Все категории включённых разделов, КРОМЕ корней самих разделов (слишком
    # широкие). Берём все уровни (и родителя «Духовые шкафы», и лист
    # «Электрические») — LLM сам выберет нужную глубину под конкретику запроса.
    include = set(INCLUDE_PREFIXES)
    cats = {h: t for h, t in links.items()
            if h not in include and any(h.startswith(p) for p in include)}

    categories: list[dict] = []
    for href, name in sorted(cats.items()):
        section = next((links.get(p, "") for p in include if href.startswith(p)), "")
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
