"""Aufräumen darf nie die letzte Sicherung mit Daten löschen (etwa nach "Neu anfangen")."""
from datetime import datetime

import pytest

from kassivo.backup import backup_db, delete_old, list_backups
from kassivo.db import connect


def _backup(tmp_path, with_member, minute):
    c = connect(tmp_path / f"src{minute}.db")
    if with_member:
        c.execute("insert into member(name) values('Anna Beispiel')")
        c.commit()
    path = backup_db(c, tmp_path / "backups", now=datetime(2026, 1, 1, 12, minute))
    c.close()
    return path.name


def _names(tmp_path):
    return {b["name"] for b in list_backups(tmp_path / "backups")}


def test_only_empty_backups_kept_then_newest_with_data_survives(tmp_path):
    older = _backup(tmp_path, True, 1)
    data = _backup(tmp_path, True, 2)
    empty1 = _backup(tmp_path, False, 3)
    empty2 = _backup(tmp_path, False, 4)
    assert delete_old(tmp_path / "backups", 1) == (2, 0)
    assert _names(tmp_path) == {data, empty2}
    assert older not in _names(tmp_path) and empty1 not in _names(tmp_path)


def test_kept_backup_with_data_means_normal_cleanup(tmp_path):
    _backup(tmp_path, True, 1)
    newest = _backup(tmp_path, True, 2)
    assert delete_old(tmp_path / "backups", 1) == (1, 0)
    assert _names(tmp_path) == {newest}


def test_unreadable_old_backup_counts_as_data(tmp_path):
    (tmp_path / "backups").mkdir()
    broken = tmp_path / "backups" / "kassivo-20250101-000000.db"
    broken.write_bytes(b"keine datenbank")
    empty = _backup(tmp_path, False, 3)
    delete_old(tmp_path / "backups", 1)
    assert _names(tmp_path) == {broken.name, empty}
