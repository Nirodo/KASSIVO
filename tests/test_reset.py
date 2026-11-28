"""Datenbank löschen und neu anfangen (#65). Nur erfundene Daten, nur tmp_path."""
import sqlite3
import sys
from pathlib import Path

import pytest

from kassivo import db as dbmod
from kassivo import restore
from kassivo.db import connect, get_setting, schema_version, set_setting
from kassivo.restore import RestoreError


def _fill(path):
    c = connect(path)
    c.execute("insert into member(name, iban) values('Erika Muster', 'XX00FAKE0000000001')")
    c.execute("insert into account(iban) values('XX00FAKE')")
    c.execute('insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key) '
              "values(1, '2026-03-01', 1700, 'k1')")
    c.execute("insert into membership_type(name) values('Ehrenmitglied')")
    c.commit()
    set_setting(c, "update_check", "1")
    c.close()


def _count(path, table):
    c = sqlite3.connect(path)
    try:
        return c.execute(f'select count(*) from "{table}"').fetchone()[0]
    finally:
        c.close()


@pytest.fixture
def target(tmp_path):
    p = tmp_path / "data" / "kassivo.db"
    _fill(p)
    return p


def _dirs(target):
    return target.parent / "restore", target.parent / "backups"


def _backups(target):
    d = target.parent / "backups"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def _fresh_reference(tmp_path):
    p = tmp_path / "ref" / "kassivo.db"
    connect(p).close()
    return p


def test_reset_creates_fresh_db_and_safety_copy(tmp_path, target):
    staging, backups = _dirs(target)
    safety = restore.reset(target, staging, backups)
    assert safety in _backups(target)
    assert _count(backups / safety, "member") == 1  # alter Stand steckt in der Kopie
    assert _count(target, "member") == 0
    assert _count(target, "transaction") == 0
    c = connect(target)
    try:
        assert schema_version(c) == dbmod.SCHEMA_VERSION
        assert get_setting(c, "update_check") is None  # Standard: aus
        assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
        # wie beim ersten Start: Standard-Mitgliedsarten und -Kategorien
        ref = connect(_fresh_reference(tmp_path))
        try:
            for table in ("membership_type", "fee_rate", "category"):
                q = f'select * from "{table}" order by 1, 2'
                assert [tuple(r) for r in c.execute(q)] == [tuple(r) for r in ref.execute(q)]
        finally:
            ref.close()
    finally:
        c.close()
    assert not any(Path(str(target) + s).exists() for s in restore.SIDECARS)


def test_reset_clears_staging_but_not_updates(tmp_path, target):
    staging, backups = _dirs(target)
    staging.mkdir()
    old = staging / ("a" * 32 + ".db")
    old.write_bytes(b"alte Vorschau")
    foreign = staging / "fremd.txt"
    foreign.write_text("bleibt")
    updates = target.parent / "updates"
    updates.mkdir()
    (updates / "kassivo-9.9.9.zip").write_bytes(b"paket")
    restore.reset(target, staging, backups)
    assert not old.exists()
    assert foreign.exists()  # nur eigene Namen werden gelöscht
    assert [p.name for p in staging.iterdir() if p.name != "fremd.txt"] == []
    assert (updates / "kassivo-9.9.9.zip").read_bytes() == b"paket"


def test_reset_safety_copy_failure_deletes_nothing(target, monkeypatch):
    staging, backups = _dirs(target)
    before = target.read_bytes()

    def boom(*a, **k):
        raise OSError("platte voll")
    monkeypatch.setattr(restore.backup, "backup_db", boom)
    with pytest.raises(RestoreError) as e:
        restore.reset(target, staging, backups)
    assert str(e.value) == restore.E_SAFETY
    assert target.read_bytes() == before
    assert not staging.exists() or list(staging.iterdir()) == []


def test_reset_swap_failure_keeps_old_db_and_names_safety(target, monkeypatch):
    staging, backups = _dirs(target)
    before = target.read_bytes()

    def boom(a, b):
        raise PermissionError("gesperrt")
    monkeypatch.setattr(restore, "_swap", boom)
    with pytest.raises(RestoreError) as e:
        restore.reset(target, staging, backups)
    assert target.read_bytes() == before
    (name,) = _backups(target)
    assert name in str(e.value)
    assert list(staging.iterdir()) == []  # keine halbe neue Datenbank liegen lassen


def test_reset_fresh_db_failure_deletes_nothing(target, monkeypatch):
    staging, backups = _dirs(target)
    before = target.read_bytes()

    def bad(conn):
        raise sqlite3.OperationalError("kaputt")
    monkeypatch.setitem(dbmod.MIGRATIONS, 2, bad)
    with pytest.raises((RestoreError, dbmod.DatabaseOpenError)):
        restore.reset(target, staging, backups)
    assert target.read_bytes() == before
    assert _backups(target) == []  # gescheitert, bevor die Kopie nötig war
    assert list(staging.iterdir()) == []


def test_reset_refuses_with_open_transaction(target):
    staging, backups = _dirs(target)
    before = target.read_bytes()
    holder = sqlite3.connect(target)
    holder.execute("begin immediate")
    holder.execute("insert into member(name) values('Offen')")
    try:
        with pytest.raises(RestoreError) as e:
            restore.reset(target, staging, backups, timeout=0.1)
        assert str(e.value) == restore.E_BUSY
        assert _backups(target) == []
    finally:
        holder.rollback()
        holder.close()
    assert target.read_bytes() == before


@pytest.mark.skipif(sys.platform != "win32", reason="Dateisperre nur unter Windows")
def test_reset_with_open_connection_on_windows(target):
    staging, backups = _dirs(target)
    holder = sqlite3.connect(target)
    holder.execute("select 1").fetchall()
    try:
        with pytest.raises(RestoreError):
            restore.reset(target, staging, backups)
    finally:
        holder.close()
    assert _count(target, "member") == 1
