"""Тесты хранилища менеджеров (allowlist, правится во время работы)."""

from __future__ import annotations

from max_ekrom_bot.managers import ManagerStore


def test_seed_and_persist(tmp_path):
    p = tmp_path / "managers.json"
    s = ManagerStore(p)
    assert s.ids() == set()
    s.seed({1, 2, 3})
    assert s.ids() == {1, 2, 3}
    assert p.exists()
    # перезагрузка с диска видит те же записи
    assert ManagerStore(p).ids() == {1, 2, 3}


def test_seed_does_not_overwrite_existing(tmp_path):
    p = tmp_path / "managers.json"
    s = ManagerStore(p)
    s.add(10, "ten")
    s.seed({1, 2})  # уже есть записи — сид игнорируется
    assert s.ids() == {10}


def test_add_remove(tmp_path):
    p = tmp_path / "managers.json"
    s = ManagerStore(p)
    assert s.add(5, "@x (Имя)") is True
    assert s.add(5) is False  # повторно — уже в списке
    assert s.contains(5)
    assert s.items()[5] == "@x (Имя)"
    assert s.remove(5) is True
    assert s.remove(5) is False
    assert not s.contains(5)


def test_broken_file_is_ignored(tmp_path):
    p = tmp_path / "managers.json"
    p.write_text("{ это не json", encoding="utf-8")
    assert ManagerStore(p).ids() == set()
