"""Догоняющий краул: запускается только когда данные реально устарели."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "crawl_if_stale", ROOT / "scripts" / "crawl_if_stale.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["crawl_if_stale"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mod():
    return _load()


class _Settings:
    def __init__(self, db):
        self.catalog_db_path = db


def _db_with_promos(tmp_path, updated_at):
    import sqlite3

    db = tmp_path / "c.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE promo_campaigns (slug TEXT, updated_at REAL)")
        con.execute("INSERT INTO promo_campaigns VALUES ('x', ?)", (updated_at,))
    return db


def test_promos_age_fresh_and_stale(mod, tmp_path):
    fresh_dir = tmp_path / "fresh"
    fresh_dir.mkdir()
    fresh = _db_with_promos(fresh_dir, time.time() - 3600)
    assert mod.promos_age_h(_Settings(fresh)) < 2

    stale_dir = tmp_path / "stale"
    stale_dir.mkdir()
    stale = _db_with_promos(stale_dir, time.time() - 40 * 3600)
    assert mod.promos_age_h(_Settings(stale)) > mod.STALE_AFTER_H


def test_promos_age_none_when_no_table(mod, tmp_path):
    # Пустая база — данных нет вовсе, краул нужен.
    assert mod.promos_age_h(_Settings(tmp_path / "empty.db")) is None


def test_catalog_age_none_when_category_missing(mod, tmp_path):
    # Хотя бы одной рабочей категории нет → считаем, что краул пропущен.
    from max_ekrom_bot.ekrom.catalog_cache import CatalogCache
    from max_ekrom_bot.models import CatalogProduct

    db = tmp_path / "cat.db"
    cache = CatalogCache(db)
    cache.upsert_many([CatalogProduct(title="Духовка", url="u", price=100)],
                      "/catalog/tekhnika_dlya_kukhni/dukhovye_shkafy/")
    assert mod.catalog_age_h(_Settings(db)) is None      # остальные категории пусты


def test_threshold_below_day(mod):
    # Порог должен срабатывать раньше, чем данные станут старше суток, иначе
    # догонять уже поздно: кэш к тому моменту успевает протухнуть.
    assert 12 <= mod.STALE_AFTER_H < 24
