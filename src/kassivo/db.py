import sqlite3
from pathlib import Path

from kassivo import backup

SCHEMA = """
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


def _seed(conn: sqlite3.Connection) -> None:
    if conn.execute("select count(*) from membership_type").fetchone()[0]:
        return
    for name, cents in (("Aktiv", 1700), ("Passiv", 1000)):
        cur = conn.execute("insert into membership_type(name) values(?)", (name,))
        conn.execute(
            "insert into fee_rate(type_id, cents, valid_from) values(?,?,?)",
            (cur.lastrowid, cents, "1900-01"),
        )
    conn.executemany(
        "insert or ignore into category(name, kind) values(?,?)",
        [("Beiträge", "in"), ("Hauptverein", "out"), ("Sonstiges", "both")],
    )


def _relax_member_iban(conn: sqlite3.Connection) -> None:
    """Ältere Datenbanken: NOT NULL an member_iban.member_id entfernen (idempotent)."""
    cols = {r["name"]: r["notnull"] for r in conn.execute("pragma table_info(member_iban)")}
    if not cols.get("member_id"):
        return
    conn.executescript("""
        begin;
        create table member_iban_new(
          iban text primary key, member_id integer references member(id));
        insert into member_iban_new(iban, member_id) select iban, member_id from member_iban;
        drop table member_iban;
        alter table member_iban_new rename to member_iban;
        commit;
    """)


# --- Schema-Version und Migrationen ---------------------------------------------------
#
# Die Version steht in PRAGMA user_version. SCHEMA oben ist die Ausgangsfassung
# (Version 1 = Schema von Release 1.1.1) und wird nicht mehr nachträglich geändert.
# Neue Datenbanken entstehen aus SCHEMA und durchlaufen danach alle Migrationen,
# genau wie bestehende Datenbanken.
#
# Neue Migration hinzufügen:
#   1. Funktion _migrate_to_N(conn) schreiben. Sie führt nur einzelne conn.execute()
#      aus, ruft nie commit() und nie executescript() auf (das würde die Transaktion
#      vorzeitig beenden).
#   2. In MIGRATIONS als {N: _migrate_to_N} eintragen.
#   3. SCHEMA_VERSION auf N erhöhen.
#   4. Test in tests/test_schema_version.py: Datenbank mit Version N-1 und Beispieldaten
#      wird korrekt umgestellt.
#
# Jeder Schritt läuft in einer eigenen Transaktion (begin immediate ... commit), die
# auch user_version setzt. user_version liegt im Datenbank-Header und wird bei einem
# Rollback mit zurückgesetzt (Test: test_failing_migration_rolls_back). Scheitert ein
# Schritt, bleiben frühere Schritte erhalten, die Version zeigt den letzten
# erfolgreichen Schritt.

BASELINE_VERSION = 1
SCHEMA_VERSION = 1
MIGRATIONS: dict = {}


class SchemaTooNewError(RuntimeError):
    """Datenbank stammt aus einer neueren KASSIVO-Version."""


class MigrationError(RuntimeError):
    """Umstellung des Datenbankformats fehlgeschlagen."""


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("pragma user_version").fetchone()[0]


def current_schema_version(migrations) -> int:
    return max(SCHEMA_VERSION, max(migrations, default=0))


def _has_user_tables(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "select count(*) from sqlite_master where type='table' and name not like 'sqlite_%'"
    ).fetchone()[0] > 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"pragma user_version = {int(version)}")


def _pre_migration_backup(conn, path, backup_dir) -> str:
    dest = Path(backup_dir) if backup_dir is not None else Path(path).parent / "backups"
    try:
        return backup.backup_db(conn, dest).name
    except Exception as exc:
        raise MigrationError(
            "Die Sicherung vor der Umstellung des Datenbankformats ist fehlgeschlagen. "
            "Die Datenbank wurde nicht verändert.") from exc


def _migrate(conn, migrations, start: int, target: int, backup_name: str | None) -> None:
    for version in range(start + 1, target + 1):
        conn.execute("begin immediate")
        try:
            migrations[version](conn)
            _set_version(conn, version)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            hint = (f" Vorher angelegte Sicherung: {backup_name}." if backup_name else "")
            raise MigrationError(
                f"Die Umstellung des Datenbankformats auf Schema {version} ist fehlgeschlagen. "
                f"Die Datenbank bleibt auf Schema {version - 1}.{hint}") from exc


def connect(path, *, migrations=None, backup_dir=None) -> sqlite3.Connection:
    migrations = MIGRATIONS if migrations is None else migrations
    target = current_schema_version(migrations)
    if sorted(migrations) != list(range(BASELINE_VERSION + 1, target + 1)):
        raise ValueError("Migrationen müssen lückenlos ab Version 2 nummeriert sein.")
    memory = str(path) == ":memory:"
    if not memory:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        version = schema_version(conn)
        if version > target:
            raise SchemaTooNewError(
                "Die Datenbank stammt aus einer neueren KASSIVO-Version "
                f"(Schema {version}, diese Version kennt {target}). "
                "Bitte KASSIVO aktualisieren.")
        fresh = not _has_user_tables(conn)
        if version == 0:
            # neu oder Altbestand 1.0.0 bis 1.1.1: wie bisher bei jedem Start
            conn.executescript(SCHEMA)
            _relax_member_iban(conn)
            _seed(conn)
            _set_version(conn, BASELINE_VERSION)
            conn.commit()
            version = BASELINE_VERSION
        if version < target:
            name = None
            if not memory and not fresh:
                name = _pre_migration_backup(conn, path, backup_dir)
            _migrate(conn, migrations, version, target, name)
    except BaseException:
        conn.close()
        raise
    return conn


def get_setting(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("select value from settings where key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "insert into settings(key, value) values(?,?) "
        "on conflict(key) do update set value=excluded.value",
        (key, value),
    )
    conn.commit()
