"""Fix-Runde nach Review (#32): Strukturprüfung, keine Fremd-Sicherung, Journal, Reste.

Nur erfundene Daten in tmp_path."""
import os
import re
import sqlite3
import time

import pytest

from kassivo import db as dbmod
from kassivo import restore
from kassivo.db import connect, schema_version
from kassivo.restore import RestoreError
from tests.helpers import make_app, make_client
from tests.test_restore import (
    _db_bytes, _export, _fill, _manifest_for, _names, _staged_files, _staging, _write, _zip,
)

TABLE_MEMBER = ("create table if not exists member(\n"
                "  id integer primary key, name text not null, street text, zip text, city text,\n"
                "  email text, phone text, iban text, eintritt text, austritt text, fee_start text);")


# --- Hilfen ---

def _current_schema_db(tmp_path, schema=dbmod.SCHEMA, extra=(), rows=()):
    """Datenbank auf aktuellem Schema, aus (veränderter) Ausgangsfassung plus Migrationen."""
    def setup(c):
        c.executescript(schema)
        for stmt in extra:
            c.execute(stmt)
        for step in range(2, dbmod.SCHEMA_VERSION + 1):
            dbmod.MIGRATIONS[step](c)
        c.execute(f"pragma user_version = {dbmod.SCHEMA_VERSION}")
        for stmt in rows:
            c.execute(stmt)
    return _db_bytes(tmp_path, setup)


def _stage_bytes(tmp_path, data, schema=dbmod.SCHEMA_VERSION):
    p = _write(tmp_path, _zip({"manifest.json": _manifest_for(data, schema_version=schema),
                               "kassivo.db": data}))
    return restore.stage_upload(p, _staging(tmp_path))


def _reject(tmp_path, data, msg=None):
    with pytest.raises(RestoreError) as e:
        _stage_bytes(tmp_path, data)
    if msg is not None:
        assert str(e.value) == msg
    assert _staged_files(tmp_path) == []


@pytest.fixture
def target(tmp_path):
    p = tmp_path / "ziel" / "kassivo.db"
    _fill(p, members=("Alt Eins",)).close()
    return p


# --- I1: Struktur gegen Referenz ---

def test_reference_shape_db_accepted(tmp_path):
    st = _stage_bytes(tmp_path, _current_schema_db(tmp_path))
    assert st.schema == dbmod.SCHEMA_VERSION


def test_db_migrated_by_connect_from_v1_accepted(tmp_path):
    """Echte, mit KASSIVO von Schema 1 umgestellte Datenbank."""
    def setup(c):
        c.executescript(dbmod.SCHEMA)
        c.execute("pragma user_version = 1")
    p = tmp_path / "alt.db"
    p.write_bytes(_db_bytes(tmp_path, setup))
    connect(p, backup_dir=tmp_path / "bk").close()
    st = _stage_bytes(tmp_path, p.read_bytes())
    assert st.schema == dbmod.SCHEMA_VERSION


def test_legacy_member_iban_not_null_is_accepted(tmp_path):
    """1.0.0: member_iban.member_id NOT NULL, KASSIVO baut die Tabelle um (Schema 0)."""
    legacy = dbmod.SCHEMA.replace(
        "iban text primary key, member_id integer references member(id));",
        "iban text primary key, member_id integer not null references member(id));")
    assert legacy != dbmod.SCHEMA

    def setup(c):
        c.executescript(legacy)
    p = tmp_path / "backups" / "kassivo-20250101-000000.db"
    p.parent.mkdir()
    p.write_bytes(_db_bytes(tmp_path, setup))
    st = restore.stage_local(p, _staging(tmp_path))
    assert st.schema == 0


@pytest.mark.parametrize("old,new", [
    (",\n  unique(account_id, dedupe_key));", ");"),
    ("amount_cents integer not null", "amount_cents real not null"),
    ("status text not null check(status in ('proposed','confirmed'))", "status text not null"),
    ("name text not null unique);\ncreate table if not exists fee_rate",
     "name text not null);\ncreate table if not exists fee_rate"),
    (TABLE_MEMBER, "create virtual table member using fts5(id, name, street, zip, city, email,"
                   " phone, iban, eintritt, austritt, fee_start);"),
])
def test_manipulated_structure_rejected(tmp_path, old, new):
    assert dbmod.SCHEMA.count(old) == 1
    _reject(tmp_path, _current_schema_db(tmp_path, dbmod.SCHEMA.replace(old, new)),
            restore.E_SCHEMA)


@pytest.mark.parametrize("extra", [
    "create virtual table notizen using fts5(x)",
    "create table fremd(x)",
    "create index ix_member_name on member(name)",
])
def test_additional_objects_rejected(tmp_path, extra):
    _reject(tmp_path, _current_schema_db(tmp_path, extra=[extra]), restore.E_SCHEMA)


@pytest.mark.parametrize("row", [
    'insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key)'
    " values(1,'2026-01-01',17.5,'k')",
    'insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key)'
    " values(1,'2026-01-01','17,00','k')",
    "insert into fee_rate(type_id, cents, valid_from) values(1, 12.25, '2026-01')",
    "insert into category_rule(category_id, field, contains, min_cents) values(1,'p','x',1.5)",
    "insert into allocation(tx_id, member_id, month, cents, status, source)"
    " values(1,1,'2026-01',x'00','proposed','auto')",
])
def test_non_integer_money_rejected(tmp_path, row):
    rows = ["insert into account(iban) values('XX00FAKE')",
            "insert into member(name) values('M')",
            "insert into membership_type(name) values('T')",
            "insert into category(name, kind) values('K','in')", row]
    _reject(tmp_path, _current_schema_db(tmp_path, rows=rows), restore.E_MONEY)


def test_integer_money_and_null_rule_limits_accepted(tmp_path):
    rows = ["insert into account(iban) values('XX00FAKE')",
            "insert into category(name, kind) values('K','in')",
            "insert into category_rule(category_id, field, contains) values(1,'p','x')",
            'insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key)'
            " values(1,'2026-01-01',-1700,'k')"]
    st = _stage_bytes(tmp_path, _current_schema_db(tmp_path, rows=rows))
    assert st.summary["transactions"] == 1


# --- I2: keine Sicherung der Fremddatei ---

def test_stage_old_schema_creates_nothing_in_backups(tmp_path):
    def setup(c):
        c.executescript(dbmod.SCHEMA)
        c.execute("pragma user_version = 1")
    data = _db_bytes(tmp_path, setup)
    st = _stage_bytes(tmp_path, data, schema=1)
    assert st.schema == 1
    assert not (_staging(tmp_path).parent / "backups").exists()
    assert not any("backups" in str(p) for p in tmp_path.rglob("kassivo-*.db"))


# --- I3: Journal-Modus und gehärtete Verbindung ---

def test_wal_backup_is_converted_to_delete_mode(tmp_path, target):
    src = tmp_path / "wal.db"
    c = _fill(src)
    assert c.execute("pragma journal_mode=wal").fetchone()[0] == "wal"
    c.close()
    data = src.read_bytes()
    assert data[18:20] == b"\x02\x02"  # WAL im Dateikopf
    st = _stage_bytes(tmp_path, data)
    assert st.path.read_bytes()[18:20] == b"\x01\x01"
    for suffix in ("-wal", "-shm", "-journal"):
        assert not os.path.exists(str(st.path) + suffix)
    restore.restore(target, st.token, _staging(tmp_path), target.parent / "backups")
    assert target.read_bytes()[18:20] == b"\x01\x01"
    assert _names(target) == ["Erika Muster"]
    for suffix in ("-wal", "-shm"):
        assert not os.path.exists(str(target) + suffix)


def test_untrusted_connection_is_hardened(tmp_path):
    p = tmp_path / "x.db"
    sqlite3.connect(p).close()
    c = restore._open(p)
    try:
        assert c.execute("pragma trusted_schema").fetchone()[0] == 0
        if hasattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE"):
            assert c.getconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE) is True
    finally:
        c.close()


# --- I4 und Aufräumen ---

def _old(path, hours=2):
    t = time.time() - hours * 3600
    os.utime(path, (t, t))


def test_startup_removes_leftovers(tmp_path):
    data = tmp_path / "data"
    _fill(data / "kassivo.db").close()
    for name in (".export-abc123", ".upload-xyz_9"):
        (data / name).mkdir()
        (data / name / "kassivo-backup.kassivo").write_bytes(b"x")
    staging = data / "restore"
    staging.mkdir()
    tok = "a" * 32
    for name in (f"{tok}.db", f"{tok}.db-journal", f"{tok}.db-wal", f"{tok}.db-shm",
                 f".{tok}.part", f".{tok}.part-journal", f"{tok}.done"):
        (staging / name).write_bytes(b"x")
    (data / ".fremd").mkdir()
    (staging / "notiz.txt").write_text("bleibt")
    make_app(data / "kassivo.db")
    assert sorted(p.name for p in data.iterdir() if p.name.startswith(".")) == [".fremd"]
    assert sorted(p.name for p in staging.iterdir()) == ["notiz.txt"]


def test_export_and_upload_remove_only_old_leftovers(tmp_path):
    data = tmp_path / "data"
    _fill(data / "kassivo.db").close()
    client = make_client(data / "kassivo.db")
    old, fresh = data / ".export-alt", data / ".upload-neu"
    old.mkdir()
    fresh.mkdir()
    _old(old)
    assert client.post("/backup/export").status_code == 200
    assert not old.exists() and fresh.exists()  # laufende Anfrage nicht zerstören
    _old(fresh)
    client.post("/backup/upload", files={"file": ("x.kassivo", b"PK\x03\x04kaputt")})
    assert not fresh.exists()


def test_export_stream_cleans_up_when_aborted(tmp_path):
    from kassivo.web import backup_routes
    d = tmp_path / ".export-x"
    d.mkdir()
    f = d / "a.kassivo"
    f.write_bytes(b"x" * (3 * 1024 * 1024))
    gen = backup_routes._stream(d, f)
    assert next(gen)
    gen.close()  # Abbruch durch den Browser
    assert not d.exists()


def test_stage_failure_removes_sidecars(tmp_path, monkeypatch):
    def setup(c):
        c.executescript(dbmod.SCHEMA)
        c.execute("pragma user_version = 1")
    data = _db_bytes(tmp_path, setup)

    def bad(conn):
        raise sqlite3.OperationalError("kaputt")
    monkeypatch.setitem(dbmod.MIGRATIONS, 2, bad)
    with pytest.raises(RestoreError):
        _stage_bytes(tmp_path, data, schema=1)
    assert _staged_files(tmp_path) == []


# --- Web: Abbrechen und Doppelklick ---

@pytest.fixture
def web(tmp_path):
    p = tmp_path / "data" / "kassivo.db"
    _fill(p, members=("Alt Eins",)).close()
    return p, make_client(p)


def _preview_token(client, content):
    r = client.post("/backup/upload", files={"file": ("x.kassivo", content)})
    assert r.status_code == 200
    return re.search(r'name="token" value="([0-9a-f]{32})"', r.text)[1]


def test_cancel_removes_staged_file(web, tmp_path):
    db_path, client = web
    token = _preview_token(client, _export(tmp_path).read_bytes())
    staged = db_path.parent / "restore" / f"{token}.db"
    assert staged.exists()
    r = client.post("/backup/cancel")
    assert r.status_code == 303 and r.headers["location"] == "/backup"
    assert not staged.exists()
    assert client.post("/backup/cancel", headers={"Origin": "http://evil.example"}).status_code == 403


def test_preview_has_cancel_form(web, tmp_path):
    _, client = web
    r = client.post("/backup/upload", files={"file": ("x.kassivo", _export(tmp_path).read_bytes())})
    assert 'action="/backup/cancel"' in r.text


def test_double_submit_reports_already_restored(web, tmp_path):
    db_path, client = web
    token = _preview_token(client, _export(tmp_path).read_bytes())
    assert client.post("/backup/restore", data={"token": token, "confirm": "1"}).status_code == 303
    before = db_path.read_bytes()
    r = client.post("/backup/restore", data={"token": token, "confirm": "1"})
    assert r.status_code == 422 and restore.E_DONE in r.text
    assert db_path.read_bytes() == before
    assert len(list((db_path.parent / "backups").iterdir())) == 1  # keine zweite Kopie


def test_restore_validation_failure_removes_staged(tmp_path, target):
    st = restore.stage_upload(_export(tmp_path), _staging(tmp_path))
    c = sqlite3.connect(st.path)
    c.execute("create table fremd(x)")
    c.commit()
    c.close()
    with pytest.raises(RestoreError):
        restore.restore(target, st.token, _staging(tmp_path), target.parent / "backups")
    assert _staged_files(tmp_path) == []
    assert _names(target) == ["Alt Eins"]
    c = connect(target)
    assert schema_version(c) == dbmod.SCHEMA_VERSION
    c.close()


# --- #57: Prüfung je Schema-Version, Trigger nur wie in der Referenz ---

T_DEMO = ("create trigger t_demo before delete on demo "
          "begin select raise(abort, 'nein'); end")


def _m4(conn):
    """Testmigration 4 (die echte kommt erst mit dem Journal): Tabelle und Trigger."""
    conn.execute("create table demo(id integer primary key, x integer not null)")
    conn.execute(T_DEMO)


@pytest.fixture
def v4(monkeypatch):
    """db.MIGRATIONS und db.SCHEMA_VERSION um die Testmigration 4 erweitert."""
    migs = {**dbmod.MIGRATIONS, 4: _m4}
    monkeypatch.setattr(dbmod, "MIGRATIONS", migs)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 4)
    return migs


def _versioned_db(tmp_path, version, migrations=None, extra=()):
    """Mit db.connect und den Migrationen bis version angelegte Datei, danach extra (roh)."""
    migrations = dbmod.MIGRATIONS if migrations is None else migrations
    p = tmp_path / "roh" / f"v{version}-{len(list(tmp_path.glob('roh/*')))}.db"
    p.parent.mkdir(exist_ok=True)
    connect(p, migrations={k: migrations[k] for k in range(2, version + 1)}).close()
    c = sqlite3.connect(p)
    for stmt in extra:
        c.execute(stmt)
    c.commit()
    c.close()
    return p.read_bytes()


def _columns(details, table):
    for kind, name, rows in details:
        if kind == "xinfo" and name == table:
            return [r[1] for r in rows]
    raise AssertionError(table)


def test_reference_per_version_differs():
    _, d1 = restore._reference(1)
    _, d3 = restore._reference(3)
    assert "source" not in _columns(d1, "allocation")
    assert "no_member" not in _columns(d1, "transaction")
    assert "source" in _columns(d3, "allocation")
    assert "no_member" in _columns(d3, "transaction")
    assert restore._reference(0) == restore._reference(1)
    assert restore._reference() == restore._reference(dbmod.SCHEMA_VERSION)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_v1_v2_v3_backups_still_accepted(tmp_path, version):
    data = _versioned_db(tmp_path, version)
    st = _stage_bytes(tmp_path, data, schema=version)
    assert st.schema == version
    assert st.summary["schema"] == dbmod.SCHEMA_VERSION
    assert st.summary["members"] == 0


def test_trigger_from_reference_accepted(tmp_path, v4):
    data = _versioned_db(tmp_path, 4)
    st = _stage_bytes(tmp_path, data, schema=4)
    assert st.schema == 4 and st.summary["schema"] == 4


def test_v3_backup_migrated_to_test_v4_gets_trigger(tmp_path, v4):
    data = _versioned_db(tmp_path, 3)
    st = _stage_bytes(tmp_path, data, schema=3)
    assert st.schema == 3
    c = sqlite3.connect(st.path)
    assert [r[0] for r in c.execute("select name from sqlite_master where type='trigger'")] == [
        "t_demo"]
    c.close()


@pytest.mark.parametrize("extra", [
    # anderer Rumpf
    ["drop trigger t_demo",
     "create trigger t_demo before delete on demo begin select raise(ignore); end"],
    # gleicher Rumpf, andere Tabelle
    ["drop trigger t_demo",
     "create trigger t_demo before delete on member begin select raise(abort, 'nein'); end"],
    # gleicher Text, anderer Name
    ["drop trigger t_demo",
     "create trigger t_demo2 before delete on demo begin select raise(abort, 'nein'); end"],
])
def test_trigger_changed_text_rejected(tmp_path, v4, extra):
    _reject_v(tmp_path, _versioned_db(tmp_path, 4, extra=extra), 4)


def test_extra_trigger_rejected(tmp_path, v4):
    extra = ["create trigger t_extra after insert on member begin delete from member; end"]
    _reject_v(tmp_path, _versioned_db(tmp_path, 4, extra=extra), 4)


def test_missing_trigger_rejected(tmp_path, v4):
    _reject_v(tmp_path, _versioned_db(tmp_path, 4, extra=["drop trigger t_demo"]), 4)


def test_trigger_in_old_version_rejected(tmp_path, v4, monkeypatch):
    """Version 3 mit t_demo: abgelehnt, bevor die Umstellung die Datei öffnet."""
    extra = ["create table demo(id integer primary key, x integer not null)", T_DEMO]
    data = _versioned_db(tmp_path, 3, extra=extra)
    opened = []
    real = dbmod.connect

    def spy(path, *a, **k):
        if str(path) != ":memory:":
            opened.append(path)
        return real(path, *a, **k)

    monkeypatch.setattr(dbmod, "connect", spy)
    _reject_v(tmp_path, data, 3)
    assert opened == []


def test_view_in_v4_rejected(tmp_path, v4):
    _reject_v(tmp_path, _versioned_db(tmp_path, 4, extra=["create view v as select 1"]), 4)


def test_negative_schema_version_rejected(tmp_path):
    """Das Manifest erlaubt nur >= 0; eine Datei aus backups/ hat keins."""
    data = _versioned_db(tmp_path, 1, extra=["pragma user_version = -1"])
    p = tmp_path / "backups" / "kassivo-20250101-000000.db"
    p.parent.mkdir()
    p.write_bytes(data)
    with pytest.raises(RestoreError) as e:
        restore.stage_local(p, _staging(tmp_path))
    assert str(e.value) == restore.E_SCHEMA
    assert _staged_files(tmp_path) == []


def test_reference_follows_migrations(v4):
    objects, details = restore._reference(4)
    assert ("table", "demo", "demo") in [o[:3] for o in objects]
    assert ("trigger", "t_demo", "demo") in [o[:3] for o in objects]
    assert _columns(details, "demo") == ["id", "x"]
    objects3, _ = restore._reference(3)
    assert "demo" not in [o[1] for o in objects3]


def _reject_v(tmp_path, data, version):
    p = _write(tmp_path, _zip({"manifest.json": _manifest_for(data, schema_version=version),
                               "kassivo.db": data}))
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert str(e.value) == restore.E_SCHEMA
    assert _staged_files(tmp_path) == []


def test_changed_trigger_rejected_before_migration(tmp_path, monkeypatch):
    """Version 4 mit verändertem t_demo, Ziel 5: abgelehnt, bevor Migration 5 läuft
    (sie löscht aus demo und würde den Trigger auslösen)."""
    def m5(conn):
        conn.execute("delete from demo")

    migs = {**dbmod.MIGRATIONS, 4: _m4, 5: m5}
    monkeypatch.setattr(dbmod, "MIGRATIONS", migs)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 5)
    extra = ["drop trigger t_demo",
             "create trigger t_demo before delete on demo begin select raise(ignore); end"]
    data = _versioned_db(tmp_path, 4, extra=extra)
    opened = []
    real = dbmod.connect

    def spy(path, *a, **k):
        if str(path) != ":memory:":
            opened.append(path)
        return real(path, *a, **k)

    monkeypatch.setattr(dbmod, "connect", spy)
    _reject_v(tmp_path, data, 4)
    assert opened == []
