"""Догоняющий краул: обновить каталог/акции, если данные устарели.

Ночной cron выполняет задание только если сервер в этот момент работает. VPS
простоял с вечера 20.08 до середины дня 21.08 — оба ночных задания пропали, кэш
каталога протух, и подбор молча ушёл на живой обход (первая страница категории,
~21 товар вместо тысячи): в живом запросе холодильник «не нашёлся» при 117
подходящих в базе.

Скрипт ставится в cron ежечасно и почти всегда ничего не делает: он лишь
смотрит возраст данных и запускает краул, когда тот действительно пропущен.
Так простой сервера или сбой расписания больше не оставляет бота со старой базой.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from max_ekrom_bot.config import get_settings  # noqa: E402
from max_ekrom_bot.ekrom.catalog import CATEGORY_URLS  # noqa: E402
from max_ekrom_bot.ekrom.catalog_cache import CatalogCache  # noqa: E402

logger = logging.getLogger("crawl_if_stale")

# Порог «пора обновить»: меньше суток — ещё свежо (ночной краул отработает сам),
# больше — задание точно пропущено.
STALE_AFTER_H = 20.0

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"
LOGS = ROOT / "data" / "logs"


def catalog_age_h(settings) -> float | None:
    """Возраст самой старой рабочей категории каталога, часов (None — пусто)."""
    cache = CatalogCache(settings.catalog_db_path)
    ages = [cache.category_age(path) for path in CATEGORY_URLS.values()]
    known = [a for a in ages if a is not None]
    if len(known) < len(ages):
        return None                      # какой-то категории нет вовсе → краулить
    return max(known) / 3600


def promos_age_h(settings) -> float | None:
    import sqlite3

    try:
        with sqlite3.connect(settings.catalog_db_path) as con:
            row = con.execute("SELECT MAX(updated_at) FROM promo_campaigns").fetchone()
    except sqlite3.Error:
        return None
    if not row or row[0] is None:
        return None
    return (time.time() - row[0]) / 3600


def run(script: str, log_name: str) -> None:
    """Запустить краул, дописывая вывод в его обычный лог."""
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / log_name).open("a", encoding="utf-8") as log:
        log.write(f"\n=== догоняющий запуск {script} "
                  f"{time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        log.flush()
        subprocess.run([str(PY), str(ROOT / "scripts" / script)],
                       stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT), check=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    settings = get_settings()

    cat_age = catalog_age_h(settings)
    if cat_age is None or cat_age > STALE_AFTER_H:
        logger.info("каталог устарел (%s ч) — запускаю краул",
                    "нет данных" if cat_age is None else f"{cat_age:.1f}")
        run("crawl_catalog.py", "crawl_catalog.log")
    else:
        logger.info("каталог свежий (%.1f ч)", cat_age)

    promo_age = promos_age_h(settings)
    if promo_age is None or promo_age > STALE_AFTER_H:
        logger.info("акции устарели (%s ч) — запускаю краул",
                    "нет данных" if promo_age is None else f"{promo_age:.1f}")
        run("crawl_promos.py", "crawl_promos.log")
    else:
        logger.info("акции свежие (%.1f ч)", promo_age)


if __name__ == "__main__":
    main()
