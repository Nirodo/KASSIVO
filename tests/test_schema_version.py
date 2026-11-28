"""Schema-Version und Migrationen (#30). Nur erfundene Daten in tmp_path."""
import hashlib
import importlib
import sqlite3

import pytest

from kassivo import db as dbmod
from kassivo.db import (
    MIGRATIONS, SCHEMA, SCHEMA_VERSION, DatabaseBusyError, InvalidDatabaseError, MigrationError,
    SchemaTooNewError, connect, current_schema_version, schema_version,
)
from tests.helpers import make_app, make_client

# Eingefrorene Ausgangsfassung (Version 1 = Release 1.1.1), unabhängig von db.SCHEMA.
BASELINE_V1 = """
create table if not exists account(
  id integer primary key, iban text not null unique, label text);
create table if not exists membership_type(
  id integer primary key, name text not null unique);
create table if not exists fee_rate(
  type_id integer not null references membership_type(id),
  cents integer not null, valid_from text not null,
  primary key(type_id, valid_from));
create table if not exists member(
  id integer primary key, name text not null, street text, zip text, city text,
  email text, phone text, iban text, eintritt text, austritt text, fee_start text);
create table if not exists member_type_history(
  member_id integer not null references member(id),
  type_id integer not null references membership_type(id),
  valid_from text not null,
  primary key(member_id, valid_from));
-- member_id NULL: IBAN ist mehrdeutig (zahlt für mehrere Mitglieder)
create table if not exists member_iban(
  iban text primary key, member_id integer references member(id));
create table if not exists category(
  id integer primary key, name text not null unique,
  kind text not null check(kind in ('in','out','both')));
create table if not exists category_rule(
  id integer primary key,
  category_id integer not null references category(id),
  field text not null, contains text not null, priority integer not null default 0,
  min_cents integer, max_cents integer);
create table if not exists import_batch(
  id integer primary key, filename text, imported_at text,
  new_count integer not null default 0, dup_count integer not null default 0);
create table if not exists "transaction"(
  id integer primary key,
  account_id integer not null references account(id),
  booking_date text not null, value_date text,
  amount_cents integer not null, bank_ref text,
  cp_name text, cp_iban text, purpose text,
  dedupe_key text not null,
  category_id integer references category(id),
  member_id integer references member(id),
  batch_id integer references import_batch(id),
  unique(account_id, dedupe_key));
create table if not exists allocation(
  id integer primary key,
  tx_id integer not null references "transaction"(id),
  member_id integer not null references member(id),
  month text not null, cents integer not null,
  status text not null check(status in ('proposed','confirmed')));
create table if not exists settings(key text primary key, value text);
"""
BASELINE_V1_SHA256 = "a663548419741d580c75ba69bfa85e1cec9dd37959032e9d91f26c47a34d0c8c"


# --- Hilfen ---

def _tables(c):
    return [r[0] for r in c.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%' "
        "order by name")]


def _master(c):
    return c.execute("select type, name, tbl_name, sql from sqlite_master order by name").fetchall()


def _dump(path):
    """sqlite_master und alle Zeilen aller Tabellen, sortiert nach rowid."""
    c = sqlite3.connect(path)
    try:
        d = {t: c.execute(f'select * from "{t}" order by rowid').fetchall() for t in _tables(c)}
        d["__master__"] = _master(c)
        return d
    finally:
        c.close()


def _rows(path):
    d = _dump(path)
    d.pop("__master__")
    return d


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


def _seed_rows(c):
    """Stammdaten wie beim ersten Start von 1.1.1."""
    c.execute("insert into membership_type(name) values('Aktiv')")
    c.execute("insert into membership_type(name) values('Passiv')")
    c.execute("insert into fee_rate(type_id, cents, valid_from) values(1, 1700, '1900-01')")
    c.execute("insert into fee_rate(type_id, cents, valid_from) values(2, 1000, '1900-01')")
    for name, kind in (("Beiträge", "in"), ("Hauptverein", "out"), ("Sonstiges", "both")):
        c.execute("insert into category(name, kind) values(?,?)", (name, kind))


def _sample_rows(c):
    """Füllt alle Tabellen (Stammdaten müssen schon da sein)."""
    c.execute("insert into member(name, iban, street, zip, city, eintritt, fee_start) values("
              "'Erika Muster', 'DE00TEST1', 'Musterweg 1', '12345', 'Musterstadt', "
              "'2020-01', '2020-01')")
    c.execute("insert into member(name, austritt) values('Max Beispiel', '2025-12')")
    c.execute("insert into member_type_history(member_id, type_id, valid_from) "
              "values(1, 1, '2020-01')")
    c.execute("insert into member_type_history(member_id, type_id, valid_from) "
              "values(2, 2, '2021-06')")
    c.execute("insert into fee_rate(type_id, cents, valid_from) values(1, 1850, '2026-01')")
    c.execute("insert into category_rule(category_id, field, contains, priority, min_cents) "
              "values(1, 'purpose', 'Beitrag', 5, 100)")
    c.execute("insert into settings(key, value) values('pruefungsstart', '2026-01')")
    c.execute("insert into account(iban, label) values('DE00KONTO', 'Kasse')")
    c.execute("insert into import_batch(filename, imported_at, new_count, dup_count) "
              "values('erfunden.xml', '2026-03-05T10:00:00', 2, 1)")
    c.execute(
        'insert into "transaction"(account_id, booking_date, value_date, amount_cents, bank_ref, '
        "cp_name, cp_iban, purpose, dedupe_key, category_id, member_id, batch_id) values("
        "1,'2026-03-01','2026-03-02',1700,'REF1','Erika Muster','DE00TEST1','Beitrag','k1',1,1,1)")
    c.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key, batch_id) '
        "values(1,'2026-03-02',-2550,'k2',1)")
    c.execute("insert into allocation(tx_id, member_id, month, cents, status) "
              "values(1,1,'2026-03',1700,'confirmed')")
    c.execute("insert into allocation(tx_id, member_id, month, cents, status) "
              "values(1,1,'2026-04',0,'proposed')")
    c.execute("insert into member_iban(iban, member_id) values('DE00TEST1', 1)")
    c.execute("insert into member_iban(iban, member_id) values('DE00GEMEINSAM', NULL)")


def _legacy_db(path, rows=True):
    """Datenbank wie von KASSIVO 1.0.0 bis 1.1.1: Ausgangsschema, user_version 0."""
    c = sqlite3.connect(path)
    c.executescript(BASELINE_V1)
    _seed_rows(c)
    if rows:
        _sample_rows(c)
    c.commit()
    c.close()


def _v1_db(path):
    """Datenbank mit Schema 1 (Release 1.1.1 nach dem ersten Start von 1.2): Altbestand,
    gestempelt auf Version 1, noch ohne Migration 2."""
    _legacy_db(path)
    c = sqlite3.connect(path)
    c.execute("pragma user_version = 1")
    c.close()


def _current_db(path):
    """Datenbank im aktuellen Schema mit Beispieldaten."""
    connect(path).close()
    c = sqlite3.connect(path)
    _sample_rows(c)
    c.commit()
    c.close()


def _as_v2(rows):
    """Erwartete Zeilen nach Migration 2: allocation bekommt source (Vorschlag = auto)."""
    out = dict(rows)
    out["allocation"] = [tuple(r) + ("auto" if r[5] == "proposed" else "manual",)
                         for r in rows["allocation"]]
    return out


def _as_v3(rows):
    """Erwartete Zeilen nach Migration 3: transaction bekommt no_member = 0."""
    out = dict(rows)
    out["transaction"] = [tuple(r) + (0,) for r in rows["transaction"]]
    return out


def _as_current(rows):
    """Zeilen aus Schema 1 nach allen Migrationen."""
    return _as_v3(_as_v2(rows))


# --- Registry, Konstanten, eingefrorenes Ausgangsschema ---

def test_registry_matches_constant():
    assert SCHEMA_VERSION == max(MIGRATIONS, default=1)
    assert current_schema_version(MIGRATIONS) == SCHEMA_VERSION
    assert current_schema_version({2: lambda c: None, 3: lambda c: None}) == 3


def test_baseline_schema_frozen():
    # Schlägt das fehl, wurde das eingefrorene Ausgangsschema (Version 1) geändert.
    # Stattdessen eine Migration hinzufügen (siehe Kommentar in db.py).
    norm = "\n".join(line.rstrip() for line in SCHEMA.splitlines())
    assert hashlib.sha256(norm.encode("utf-8")).hexdigest() == BASELINE_V1_SHA256
    assert SCHEMA == BASELINE_V1


def test_fresh_schema_equals_legacy_stamped(tmp_path):
    fresh, legacy = tmp_path / "neu.db", tmp_path / "alt.db"
    connect(fresh).close()
    _legacy_db(legacy, rows=False)
    connect(legacy).close()
    assert _dump(fresh) == _dump(legacy)
    assert _raw_version(fresh) == _raw_version(legacy) == SCHEMA_VERSION


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

def test_legacy_db_stamped_then_migrated_with_backup(tmp_path):
    p = tmp_path / "k.db"
    _legacy_db(p)
    assert _raw_version(p) == 0
    before = _rows(p)
    connect(p).close()
    assert _raw_version(p) == SCHEMA_VERSION == 3
    assert _rows(p) == _as_current(before)
    names = _backups(p)
    assert len(names) == 1
    bk = p.parent / "backups" / names[0]
    assert _raw_version(bk) == 1 and _rows(bk) == before
    after = _dump(p)
    connect(p).close()
    assert _dump(p) == after and _raw_version(p) == 3
    assert len(_backups(p)) == 1


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
    before = _rows(p)
    c = connect(p)
    notnull = {r["name"]: r["notnull"] for r in c.execute("pragma table_info(member_iban)")}
    assert notnull["member_id"] == 0
    c.close()
    assert _rows(p) == _as_current(before)
    assert _raw_version(p) == SCHEMA_VERSION


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
    assert "Ursache: ValueError: kaputt" in msg


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
    assert c.execute("select count(*) from allocation").fetchone()[0] == 2
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
    with pytest.raises(MigrationError) as ei:
        connect(p, migrations={2: lambda conn: ran.append(1)})
    assert ran == []
    msg = str(ei.value)
    assert "Das Datenformat wurde nicht umgestellt." in msg
    assert "Ursache: OSError" in msg and "voll" not in msg
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
    assert _raw_version(p) == SCHEMA_VERSION


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


# --- Doku ---

def test_readme_mentions_schema_backup():
    from pathlib import Path
    t = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    assert "automatisch eine Sicherung" in t and "neueren KASSIVO-Version" in t
    assert "Versionen bis 1.1.1 prüfen die Schema-Version nicht" in t
    assert "sichert nicht automatisch" not in t
    assert "keine automatische Sicherung" not in t


# --- Fix-Runde 1: zwei Instanzen, Sperre, ungültige Datei ---

def _race(monkeypatch, second):
    """Führt second() aus, direkt nachdem die erste Verbindung die Version gelesen hat."""
    orig = dbmod.schema_version
    state = {"done": False}

    def wrapped(conn):
        v = orig(conn)
        if not state["done"]:
            state["done"] = True
            second()
        return v

    monkeypatch.setattr(dbmod, "schema_version", wrapped)


def test_parallel_instance_migrates_once(tmp_path, monkeypatch):
    p = tmp_path / "k.db"
    _v1_db(p)
    runs = []

    def times100(conn):
        runs.append(1)
        conn.execute("update allocation set cents = cents * 100")

    migs = {2: times100}
    _race(monkeypatch, lambda: connect(p, migrations=migs).close())
    c = connect(p, migrations=migs)
    assert schema_version(c) == 2
    assert [r[0] for r in c.execute("select cents from allocation order by id")] == [170000, 0]
    c.close()
    assert runs == [1]
    assert len(_backups(p)) == 1


def test_parallel_instance_too_new_after_lock(tmp_path, monkeypatch):
    p = tmp_path / "k.db"
    _v1_db(p)

    def bump():
        c = sqlite3.connect(p)
        c.execute("pragma user_version = 7")
        c.close()

    _race(monkeypatch, bump)
    with pytest.raises(SchemaTooNewError):
        connect(p, migrations={2: _add_note})
    assert _raw_version(p) == 7
    assert _backups(p) == []


def test_parallel_instance_legacy_stamp_once(tmp_path, monkeypatch):
    p = tmp_path / "k.db"
    _legacy_db(p)
    before = _rows(p)
    _race(monkeypatch, lambda: connect(p).close())
    connect(p).close()
    assert _raw_version(p) == SCHEMA_VERSION and _rows(p) == _as_current(before)


def test_parallel_fresh_create_once(tmp_path, monkeypatch):
    p = tmp_path / "k.db"
    _race(monkeypatch, lambda: connect(p).close())
    c = connect(p)
    assert c.execute("select count(*) from membership_type").fetchone()[0] == 2
    assert schema_version(c) == SCHEMA_VERSION
    c.close()


def _hold_lock(p):
    other = sqlite3.connect(p, isolation_level=None)
    other.execute("begin immediate")
    return other


def test_locked_db_gives_busy_error_on_migration(tmp_path):
    p = tmp_path / "k.db"
    _v1_db(p)
    other = _hold_lock(p)
    try:
        with pytest.raises(DatabaseBusyError) as ei:
            connect(p, migrations={2: _add_note}, timeout=0.1)
    finally:
        other.rollback()
        other.close()
    assert isinstance(ei.value, RuntimeError)
    assert str(ei.value) == ("Die Datenbank ist von einem anderen Programm oder einer zweiten "
                             "KASSIVO-Instanz geöffnet. Bitte das andere Fenster schließen "
                             "und erneut starten.")
    assert _raw_version(p) == 1 and _backups(p) == []


def test_locked_legacy_db_gives_busy_error(tmp_path):
    p = tmp_path / "k.db"
    _legacy_db(p)
    other = _hold_lock(p)
    try:
        with pytest.raises(DatabaseBusyError):
            connect(p, timeout=0.1)
    finally:
        other.rollback()
        other.close()
    assert _raw_version(p) == 0


def test_current_db_opens_while_other_holds_write_lock(tmp_path):
    p = tmp_path / "k.db"
    _current_db(p)
    other = _hold_lock(p)
    try:
        connect(p, timeout=0.1).close()
    finally:
        other.rollback()
        other.close()


def test_versioned_file_without_tables_rejected(tmp_path):
    p = tmp_path / "k.db"
    c = sqlite3.connect(p)
    c.execute("pragma user_version = 1")
    c.close()
    h = _hash(p)
    with pytest.raises(InvalidDatabaseError) as ei:
        connect(p)
    assert str(ei.value) == "Die Datei ist keine gültige KASSIVO-Datenbank."
    assert isinstance(ei.value, RuntimeError)
    assert _hash(p) == h


def test_non_sqlite_file_rejected(tmp_path):
    p = tmp_path / "k.db"
    p.write_bytes(b"kein sqlite" * 200)
    with pytest.raises(InvalidDatabaseError):
        connect(p)


def test_main_busy_exits_1_without_traceback(tmp_path, monkeypatch, capsys):
    p = tmp_path / "k.db"
    _legacy_db(p)
    monkeypatch.setenv("KASSIVO_DB", str(p))
    main_mod = importlib.import_module("kassivo.__main__")
    calls = []
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda app, **kw: calls.append(kw))

    def busy(*a, **k):
        raise DatabaseBusyError("Die Datenbank ist von einem anderen Programm belegt.")

    monkeypatch.setattr(main_mod, "create_app", busy)
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 1
    cap = capsys.readouterr()
    assert "anderen Programm" in cap.err and "Traceback" not in cap.out + cap.err
    assert calls == []


def test_backup_page_program_version_from_registry(tmp_path, monkeypatch):
    p = tmp_path / "k.db"
    monkeypatch.setattr(dbmod, "MIGRATIONS", {2: _add_note})
    r = make_client(p).get("/backup")
    assert "Datenbank-Schema: Version 2 (Programm: 2)" in r.text


# --- Migration 2: allocation.source (#27) ---

def test_migration_2_adds_source_with_data(tmp_path):
    p = tmp_path / "k.db"
    _v1_db(p)
    before = _dump(p)
    rows_before = _rows(p)
    c = connect(p, migrations={2: MIGRATIONS[2]})  # nur Migration 2
    assert schema_version(c) == 2
    got = [tuple(r) for r in c.execute(
        "select id, tx_id, member_id, month, cents, status, source from allocation order by id")]
    assert got == [(1, 1, 1, "2026-03", 1700, "confirmed", "manual"),
                   (2, 1, 1, "2026-04", 0, "proposed", "auto")]
    for t in rows_before:
        if t != "allocation":
            assert c.execute(f'select count(*) from "{t}"').fetchone()[0] == len(rows_before[t])
    # neue Zeile ohne source: manual; ungültiger Wert wird abgelehnt
    c.execute("insert into allocation(tx_id, member_id, month, cents, status)"
              " values(1, 1, '2026-05', 1, 'proposed')")
    assert c.execute("select source from allocation where month='2026-05'").fetchone()[0] == "manual"
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into allocation(tx_id, member_id, month, cents, status, source)"
                  " values(1, 1, '2026-06', 1, 'proposed', 'x')")
    c.rollback()
    c.close()
    names = _backups(p)
    assert len(names) == 1
    bk = p.parent / "backups" / names[0]
    assert _raw_version(bk) == 1 and _dump(bk) == before


def test_migrations_registered():
    assert SCHEMA_VERSION == 3 and set(MIGRATIONS) == {2, 3}


# --- Migration 3: transaction.no_member (#6) ---

def _v2_db(path):
    """Datenbank mit Schema 2 (Release 1.2.0) und Beispieldaten."""
    _v1_db(path)
    connect(path, migrations={2: MIGRATIONS[2]}, backup_dir=path.parent / "alt").close()


def test_migration_3_adds_no_member_with_data(tmp_path):
    p = tmp_path / "k.db"
    _v2_db(p)
    assert _raw_version(p) == 2
    before = _dump(p)
    rows_before = _rows(p)
    c = connect(p)
    assert schema_version(c) == 3
    assert [tuple(r) for r in c.execute(
        'select id, member_id, no_member from "transaction" order by id')] == [
        (1, 1, 0), (2, None, 0)]
    assert _rows(p) == _as_v3(rows_before)
    # neue Zeile ohne Angabe: 0; nur 0 und 1 erlaubt
    c.execute('insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key)'
              " values(1, '2026-04-01', 100, 'k3')")
    assert c.execute('select no_member from "transaction" where dedupe_key=?',
                     ("k3",)).fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError):
        c.execute('insert into "transaction"(account_id, booking_date, amount_cents,'
                  " dedupe_key, no_member) values(1, '2026-04-01', 100, 'k4', 2)")
    c.rollback()
    c.close()
    names = _backups(p)
    assert len(names) == 1
    bk = p.parent / "backups" / names[0]
    assert _raw_version(bk) == 2 and _dump(bk) == before


def test_migration_3_idempotent_on_second_start(tmp_path):
    p = tmp_path / "k.db"
    _v2_db(p)
    connect(p).close()
    after = _dump(p)
    connect(p).close()
    assert _dump(p) == after and len(_backups(p)) == 1


# --- Schritte mit ausgeschalteten Fremdschlüsseln (fk_off, #57) ---

def _fk_pragma(c):
    return c.execute("pragma foreign_keys").fetchone()[0]


def _rebuild_account(conn):
    """Baut account neu (mit Spalte note), auf die "transaction" verweist."""
    conn.execute("create table account_new(id integer primary key, iban text not null unique,"
                 " label text, note text)")
    conn.execute("insert into account_new(id, iban, label) select id, iban, label from account")
    conn.execute("drop table account")
    conn.execute("alter table account_new rename to account")


_rebuild_account.fk_off = True


def _spy_fk_on_close(monkeypatch, path):
    """Merkt pragma foreign_keys der Hauptverbindung von connect(path) vor dem Schließen.

    Hauptverbindung ist die erste Verbindung zu path; Sicherungen öffnen eigene."""
    seen = []
    made = []
    orig_close = sqlite3.Connection.close
    target = str(path)

    class Spy(sqlite3.Connection):
        def close(self):
            if made and self is made[0]:
                seen.append(self.execute("pragma foreign_keys").fetchone()[0])
            orig_close(self)

    real = sqlite3.connect

    def spy_connect(*a, **k):
        if a and str(a[0]) == target:
            c = real(*a, factory=Spy, **k)
            made.append(c)
            return c
        return real(*a, **k)

    monkeypatch.setattr(dbmod.sqlite3, "connect", spy_connect)
    return seen


def test_fk_off_step_can_rebuild_referenced_table(tmp_path):
    p = tmp_path / "k.db"
    _v1_db(p)
    before = _rows(p)
    c = connect(p, migrations={2: _rebuild_account})
    assert schema_version(c) == 2
    assert _fk_pragma(c) == 1
    c.close()
    after = _rows(p)
    assert after["account"] == [tuple(r) + (None,) for r in before["account"]]
    after.pop("account")
    before.pop("account")
    assert after == before


def test_fk_off_step_with_violation_rolls_back(tmp_path):
    p = tmp_path / "k.db"
    _v1_db(p)
    before = _dump(p)

    def orphan(conn):
        conn.execute("insert into allocation(tx_id, member_id, month, cents, status)"
                     " values(999, 1, '2026-05', 100, 'confirmed')")

    orphan.fk_off = True
    with pytest.raises(MigrationError) as ei:
        connect(p, migrations={2: orphan})
    msg = str(ei.value)
    assert "Schema 2" in msg and "Fremdschlüssel" in msg
    assert "999" not in msg
    assert _raw_version(p) == 1
    assert _dump(p) == before


def test_fk_off_step_restores_pragma_after_violation(tmp_path, monkeypatch):
    """Nach dem Fehler ist foreign_keys auf der Verbindung wieder an (vor dem Schließen)."""
    p = tmp_path / "k.db"
    _v1_db(p)
    seen = _spy_fk_on_close(monkeypatch, p)

    def orphan(conn):
        conn.execute("insert into allocation(tx_id, member_id, month, cents, status)"
                     " values(999, 1, '2026-05', 100, 'confirmed')")

    orphan.fk_off = True
    with pytest.raises(MigrationError):
        connect(p, migrations={2: orphan})
    assert seen == [1]


def test_fk_off_step_exception_restores_fk_pragma(tmp_path, monkeypatch):
    p = tmp_path / "k.db"
    _v1_db(p)
    seen = _spy_fk_on_close(monkeypatch, p)
    inside = []

    def boom(conn):
        inside.append(_fk_pragma(conn))
        raise RuntimeError("kaputt")

    boom.fk_off = True
    with pytest.raises(MigrationError):
        connect(p, migrations={2: boom})
    assert inside == [0]
    assert seen == [1]
    assert _raw_version(p) == 1


def test_fk_off_step_after_backup_takes_one_backup(tmp_path):
    p = tmp_path / "k.db"
    _v1_db(p)
    c = connect(p, migrations={2: _rebuild_account})
    c.close()
    assert len(_backups(p)) == 1
    assert _raw_version(p.parent / "backups" / _backups(p)[0]) == 1


def test_fk_off_step_mixed_with_normal_steps(tmp_path):
    p = tmp_path / "k.db"
    _v1_db(p)
    seen = []

    def m3(conn):
        seen.append(_fk_pragma(conn))
        _add_note(conn)

    c = connect(p, migrations={2: _rebuild_account, 3: m3})
    assert schema_version(c) == 3 and seen == [1] and _fk_pragma(c) == 1
    c.close()
    assert len(_backups(p)) == 1


def test_fk_off_parallel_instance_skips(tmp_path, monkeypatch):
    p = tmp_path / "k.db"
    _v1_db(p)
    runs = []

    def times100(conn):
        runs.append(1)
        conn.execute("update allocation set cents = cents * 100")

    times100.fk_off = True
    migs = {2: times100}
    _race(monkeypatch, lambda: connect(p, migrations=migs).close())
    c = connect(p, migrations=migs)
    assert schema_version(c) == 2 and _fk_pragma(c) == 1
    assert [r[0] for r in c.execute("select cents from allocation order by id")] == [170000, 0]
    c.close()
    assert runs == [1]
    assert len(_backups(p)) == 1


def test_normal_step_keeps_fk_on(tmp_path):
    p = tmp_path / "k.db"
    _v1_db(p)
    seen = []

    def m2(conn):
        seen.append(_fk_pragma(conn))

    connect(p, migrations={2: m2}).close()
    assert seen == [1]


def test_migration_comment_mentions_fk_off():
    src = importlib.import_module("kassivo.db").__file__
    text = open(src, encoding="utf-8").read()
    assert "Tabellen neu aufbauen nur in einem Schritt mit `fk_off = True`." in text


def test_fk_check_lists_tables_only(tmp_path):
    c = connect(tmp_path / "k.db")
    assert dbmod._fk_check(c) == []
    c.execute("pragma foreign_keys = OFF")
    c.execute("insert into allocation(tx_id, member_id, month, cents, status)"
              " values(77, 88, '2026-05', 100, 'confirmed')")
    assert dbmod._fk_check(c) == ["allocation"]
    c.rollback()
    c.close()
