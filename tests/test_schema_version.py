"""Schema-Version und Migrationen (#30). Nur erfundene Daten in tmp_path."""
import hashlib
import importlib
import sqlite3

import pytest

from kassivo import db as dbmod
from kassivo.db import (
    MIGRATIONS, SCHEMA, SCHEMA_VERSION, MigrationError, SchemaTooNewError, _seed, connect,
    current_schema_version, schema_version,
)
from tests.helpers import make_app, make_client


# --- Hilfen ---

def _tables(c):
    return [r[0] for r in c.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%' "
        "order by name")]


def _dump(path):
    """Alle Zeilen aller Tabellen, sortiert nach rowid."""
    c = sqlite3.connect(path)
    try:
        return {t: c.execute(f'select * from "{t}" order by rowid').fetchall() for t in _tables(c)}
    finally:
        c.close()


def _raw_version(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("pragma user_version").fetchone()[0]
    finally:
        c.close()


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backups(path):
    d = path.parent / "backups"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def _sample_rows(c):
    c.execute("insert into member(name, iban) values('Erika Muster', 'DE00TEST1')")
    c.execute("insert into member(name) values('Max Beispiel')")
    c.execute("insert into account(iban, label) values('DE00KONTO', 'Kasse')")
    c.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key, '
        "cp_name, purpose, member_id) values(1,'2026-03-01',1700,'k1','Erika Muster','Beitrag',1)")
    c.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key) '
        "values(1,'2026-03-02',-2550,'k2')")
    c.execute("insert into allocation(tx_id, member_id, month, cents, status) "
              "values(1,1,'2026-03',1700,'confirmed')")
    c.execute("insert into member_iban(iban, member_id) values('DE00TEST1', 1)")
    c.execute("insert into member_iban(iban, member_id) values('DE00GEMEINSAM', NULL)")


def _legacy_db(path):
    """Datenbank wie von KASSIVO 1.0.0 bis 1.1.1: aktuelles Schema, user_version 0."""
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    _seed(c)
    _sample_rows(c)
    c.commit()
    c.close()


def _v1_db(path):
    connect(path).close()
    c = sqlite3.connect(path)
    _sample_rows(c)
    c.commit()
    c.close()


# --- Registry und Konstanten ---

def test_registry_matches_constant():
    assert SCHEMA_VERSION == 1
    assert current_schema_version(MIGRATIONS) == SCHEMA_VERSION
    assert current_schema_version({}) == SCHEMA_VERSION
    assert current_schema_version({2: lambda c: None, 3: lambda c: None}) == 3


def test_gap_in_migrations_rejected(tmp_path):
    with pytest.raises(ValueError):
        connect(tmp_path / "k.db", migrations={3: lambda c: None})


# --- Neue Datenbank ---

def test_fresh_file_db(tmp_path):
    p = tmp_path / "k.db"
    c = connect(p)
    assert schema_version(c) == SCHEMA_VERSION
    assert {"member", "transaction", "allocation", "member_iban"} <= set(_tables(c))
    assert c.execute("select count(*) from membership_type").fetchone()[0] == 2
    c.close()
    assert _raw_version(p) == SCHEMA_VERSION
    assert _backups(p) == []


def test_fresh_memory_db():
    c = connect(":memory:")
    assert schema_version(c) == SCHEMA_VERSION
    assert c.execute("select count(*) from category").fetchone()[0] == 3
    c.close()


def test_fresh_db_runs_injected_migrations_without_backup(tmp_path):
    p = tmp_path / "k.db"
    seen = []
    c = connect(p, migrations={2: lambda conn: seen.append(2)})
    assert seen == [2] and schema_version(c) == 2
    c.close()
    assert _backups(p) == []


# --- Altbestand 1.0.0 bis 1.1.1 ---

def test_legacy_db_stamped_without_changes(tmp_path):
    p = tmp_path / "k.db"
    _legacy_db(p)
    assert _raw_version(p) == 0
    before = _dump(p)
    connect(p).close()
    assert _raw_version(p) == 1
    assert _dump(p) == before
    assert _backups(p) == []
    connect(p).close()
    assert _dump(p) == before and _raw_version(p) == 1
    assert _backups(p) == []


def test_legacy_db_with_not_null_member_iban_relaxed(tmp_path):
    p = tmp_path / "k.db"
    _legacy_db(p)
    c = sqlite3.connect(p)
    c.executescript("""
        delete from member_iban where member_id is null;
        create table old(iban text primary key,
                         member_id integer not null references member(id));
        insert into old select * from member_iban;
        drop table member_iban;
        alter table old rename to member_iban;
    """)
    c.close()
    before = _dump(p)
    c = connect(p)
    notnull = {r["name"]: r["notnull"] for r in c.execute("pragma table_info(member_iban)")}
    assert notnull["member_id"] == 0
    c.close()
    assert _dump(p) == before
    assert _raw_version(p) == 1


# --- Zu neue Datenbank ---

def test_too_new_db_refused_untouched(tmp_path):
    p = tmp_path / "k.db"
    _v1_db(p)
    c = sqlite3.connect(p)
    c.execute("pragma user_version = 99")
    c.close()
    h = _hash(p)
    with pytest.raises(SchemaTooNewError) as ei:
        connect(p)
    msg = str(ei.value)
    assert msg == ("Die Datenbank stammt aus einer neueren KASSIVO-Version "
                   f"(Schema 99, diese Version kennt {SCHEMA_VERSION}). "
                   "Bitte KASSIVO aktualisieren.")
    assert isinstance(ei.value, RuntimeError)
    assert _hash(p) == h
    assert _backups(p) == []
    assert not (tmp_path / "k.db-journal").exists()


# --- Migrationen ---

def _add_note(conn):
    conn.execute("alter table member add column note text")
    conn.execute("update member set note = 'v2:' || name")


def _double_cents(conn):
    conn.execute("alter table allocation add column cents_x2 integer")
    conn.execute("update allocation set cents_x2 = cents * 2")


def test_migrations_applied_in_order_with_one_backup(tmp_path):
    p = tmp_path / "k.db"
    _v1_db(p)
    before = _dump(p)
    order = []

    def m2(conn):
        order.append(2)
        _add_note(conn)

    def m3(conn):
        order.append(3)
        # setzt Schritt 2 voraus
        assert conn.execute("select note from member where id=1").fetchone()[0] == "v2:Erika Muster"
        _double_cents(conn)

    c = connect(p, migrations={3: m3, 2: m2})
    assert order == [2, 3]
    assert schema_version(c) == 3
    assert c.execute("select note from member order by id").fetchall()[1][0] == "v2:Max Beispiel"
    assert c.execute("select cents, cents_x2 from allocation").fetchone()[:] == (1700, 3400)
    assert c.execute('select amount_cents from "transaction" order by id').fetchall()[1][0] == -2550
    c.close()

    names = _backups(p)
    assert len(names) == 1
    assert dbmod.backup.NAME_RE.fullmatch(names[0])
    bk = p.parent / "backups" / names[0]
    assert _raw_version(bk) == 1
    assert _dump(bk) == before

    order.clear()
    c = connect(p, migrations={2: m2, 3: m3})
    assert order == [] and schema_version(c) == 3
    c.close()
    assert len(_backups(p)) == 1


def test_backup_dir_override(tmp_path):
    p = tmp_path / "k.db"
    _v1_db(p)
    other = tmp_path / "anders"
    connect(p, migrations={2: _add_note}, backup_dir=other).close()
    assert len(list(other.iterdir())) == 1
    assert _backups(p) == []


def test_failing_migration_rolls_back(tmp_path):
    p = tmp_path / "k.db"
    _v1_db(p)
    before = _dump(p)

    def bad(conn):
        _add_note(conn)
        conn.execute("delete from allocation")
        raise ValueError("kaputt")

    with pytest.raises(MigrationError) as ei:
        connect(p, migrations={2: bad})
    assert isinstance(ei.value, RuntimeError)
    assert _raw_version(p) == 1
    assert _dump(p) == before
    c = sqlite3.connect(p)
    assert "note" not in [r[1] for r in c.execute("pragma table_info(member)")]
    c.close()
    names = _backups(p)
    assert len(names) == 1
    msg = str(ei.value)
    assert names[0] in msg
    assert str(tmp_path) not in msg and "backups" not in msg
    assert "Schema 2" in msg


def test_second_step_fails_first_stays(tmp_path):
    """Jeder Schritt ist atomar: die Version zeigt den letzten erfolgreichen Schritt."""
    p = tmp_path / "k.db"
    _v1_db(p)

    def bad(conn):
        _double_cents(conn)
        raise ValueError("kaputt")

    with pytest.raises(MigrationError) as ei:
        connect(p, migrations={2: _add_note, 3: bad})
    assert "Schema 3" in str(ei.value)
    assert _raw_version(p) == 2
    c = sqlite3.connect(p)
    assert "note" in [r[1] for r in c.execute("pragma table_info(member)")]
    assert "cents_x2" not in [r[1] for r in c.execute("pragma table_info(allocation)")]
    assert c.execute("select count(*) from allocation").fetchone()[0] == 1
    c.close()
    assert len(_backups(p)) == 1

    # nächster Start mit reparierter Migration: nur Schritt 3, neue Sicherung vorher
    c = connect(p, migrations={2: _add_note, 3: _double_cents})
    assert schema_version(c) == 3
    c.close()
    assert len(_backups(p)) == 2


def test_backup_failure_blocks_migration(tmp_path, monkeypatch):
    p = tmp_path / "k.db"
    _v1_db(p)
    before = _dump(p)
    ran = []

    def boom(conn, dest):
        raise OSError("voll")

    monkeypatch.setattr(dbmod.backup, "backup_db", boom)
    with pytest.raises(MigrationError):
        connect(p, migrations={2: lambda conn: ran.append(1)})
    assert ran == []
    assert _raw_version(p) == 1 and _dump(p) == before


def test_memory_db_migrates_without_backup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = connect(":memory:", migrations={2: _add_note})
    assert schema_version(c) == 2
    c.close()
    assert list(tmp_path.iterdir()) == []


# --- Start ---

def _too_new(p):
    _v1_db(p)
    c = sqlite3.connect(p)
    c.execute("pragma user_version = 99")
    c.close()


def test_app_refuses_too_new(tmp_path):
    p = tmp_path / "k.db"
    _too_new(p)
    with pytest.raises(SchemaTooNewError):
        make_app(p)


def test_app_opens_db_once_at_start(tmp_path):
    p = tmp_path / "k.db"
    _legacy_db(p)
    make_app(p)
    assert _raw_version(p) == 1


def test_main_too_new_exits_1_without_traceback(tmp_path, monkeypatch, capsys):
    p = tmp_path / "k.db"
    _too_new(p)
    monkeypatch.setenv("KASSIVO_DB", str(p))
    main_mod = importlib.import_module("kassivo.__main__")
    calls = []
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda app, **kw: calls.append(kw))
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 1
    cap = capsys.readouterr()
    out = cap.out + cap.err
    assert str(p.resolve()) in out
    assert "neueren KASSIVO-Version" in out and "Schema 99" in out
    assert "Traceback" not in out
    assert calls == []


def test_main_migration_error_exits_1(tmp_path, monkeypatch, capsys):
    p = tmp_path / "k.db"
    _v1_db(p)
    monkeypatch.setenv("KASSIVO_DB", str(p))

    def bad(conn):
        raise ValueError("kaputt")

    monkeypatch.setattr(dbmod, "MIGRATIONS", {2: bad})
    main_mod = importlib.import_module("kassivo.__main__")
    calls = []
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda app, **kw: calls.append(kw))
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 1
    cap = capsys.readouterr()
    assert "Sicherung" in cap.out + cap.err and "Traceback" not in cap.out + cap.err
    assert calls == []


# --- Oberfläche ---

def test_backup_page_shows_schema_line(tmp_path):
    r = make_client(tmp_path / "k.db").get("/backup")
    assert r.status_code == 200
    assert (f"Datenbank-Schema: Version {SCHEMA_VERSION} (Programm: {SCHEMA_VERSION})"
            in r.text)
