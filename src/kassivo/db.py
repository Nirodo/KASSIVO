import sqlite3
from pathlib import Path

from kassivo import backup, settings

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
    # läuft in der Transaktion des Aufrufers (connect), daher kein executescript
    conn.execute("create table member_iban_new("
                 "iban text primary key, member_id integer references member(id))")
    conn.execute("insert into member_iban_new(iban, member_id) "
                 "select iban, member_id from member_iban")
    conn.execute("drop table member_iban")
    conn.execute("alter table member_iban_new rename to member_iban")


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
# auch user_version setzt. Unter der Sperre wird die Version neu gelesen: hat eine
# zweite KASSIVO-Instanz den Schritt schon ausgeführt, wird er übersprungen.
# user_version liegt im Datenbank-Header und wird bei einem
# Rollback mit zurückgesetzt (Test: test_failing_migration_rolls_back). Scheitert ein
# Schritt, bleiben frühere Schritte erhalten, die Version zeigt den letzten
# erfolgreichen Schritt.

def _migrate_to_2(conn: sqlite3.Connection) -> None:
    """Schema 2 (#27): allocation.source, wer eine Verteilung gemacht hat.

    'auto' = KASSIVO (Vorschlag oder automatisch bestätigt), 'manual' = Nutzer.
    Bestehende Vorschläge stammen vom System ('auto'). Bei bestätigten Zeilen ist nicht
    bekannt, wer entschieden hat: sie bleiben 'manual' und gelten nicht als automatisch.
    """
    conn.execute("alter table allocation add column source text not null default 'manual'"
                 " check (source in ('auto','manual'))")
    conn.execute("update allocation set source='auto' where status='proposed'")


BASELINE_VERSION = 1
SCHEMA_VERSION = 2
MIGRATIONS: dict = {2: _migrate_to_2}


class DatabaseOpenError(RuntimeError):
    """Datenbank kann nicht geöffnet werden; die Meldung ist für Nutzer gedacht."""


class SchemaTooNewError(DatabaseOpenError):
    """Datenbank stammt aus einer neueren KASSIVO-Version."""


class MigrationError(DatabaseOpenError):
    """Umstellung des Datenbankformats fehlgeschlagen."""


class DatabaseBusyError(DatabaseOpenError):
    """Datenbank ist von einer anderen Verbindung gesperrt."""


class InvalidDatabaseError(DatabaseOpenError):
    """Datei ist keine KASSIVO-Datenbank."""


E_BUSY = ("Die Datenbank ist von einem anderen Programm oder einer zweiten KASSIVO-Instanz "
          "geöffnet. Bitte das andere Fenster schließen und erneut starten.")
E_INVALID = "Die Datei ist keine gültige KASSIVO-Datenbank."


def schema_version(conn: sqlite3.Connection) -> int:
    # fetchall: Anweisung ganz abarbeiten, damit keine Lesesperre offen bleibt
    return conn.execute("pragma user_version").fetchall()[0][0]


def current_schema_version(migrations) -> int:
    return max(SCHEMA_VERSION, max(migrations, default=0))


def _has_user_tables(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "select count(*) from sqlite_master where type='table' and name not like 'sqlite_%'"
    ).fetchall()[0][0] > 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"pragma user_version = {int(version)}")


def _statements(script: str):
    """Zerlegt ein SQL-Skript in einzelne Anweisungen (für execute in einer Transaktion)."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            yield buf.strip()
            buf = ""


def _too_new(version: int, target: int) -> SchemaTooNewError:
    return SchemaTooNewError(
        "Die Datenbank stammt aus einer neueren KASSIVO-Version "
        f"(Schema {version}, diese Version kennt {target}). Bitte KASSIVO aktualisieren.")


def _cause(exc: BaseException) -> str:
    """Kurze Ursache für die Meldung. Bei OSError nur der Typ (die Meldung enthält oft Pfade)."""
    name = type(exc).__name__
    text = str(exc).strip()
    if isinstance(exc, OSError) or not text:
        return f"Ursache: {name}."
    return f"Ursache: {name}: {text[:200]}"


def _lock(conn: sqlite3.Connection, target: int) -> int:
    """Schreibsperre holen und die Version unter der Sperre neu lesen."""
    conn.execute("begin immediate")
    version = schema_version(conn)
    if version > target:
        conn.rollback()
        raise _too_new(version, target)
    return version


def _stamp_baseline(conn: sqlite3.Connection, target: int) -> bool:
    """Version 0: neu anlegen oder Altbestand 1.0.0 bis 1.1.1 übernehmen. True = neu angelegt."""
    if _lock(conn, target) != 0:
        conn.rollback()  # eine andere Instanz war schneller
        return False
    try:
        fresh = not _has_user_tables(conn)
        for stmt in _statements(SCHEMA):
            conn.execute(stmt)
        _relax_member_iban(conn)
        _seed(conn)
        _set_version(conn, BASELINE_VERSION)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return fresh


def _pre_migration_backup(conn, path, backup_dir) -> str:
    dest = Path(backup_dir) if backup_dir is not None else settings.backup_dir(Path(path))
    try:
        return backup.backup_db(conn, dest).name
    except Exception as exc:
        raise MigrationError(
            "Die Sicherung vor der Umstellung des Datenbankformats ist fehlgeschlagen. "
            f"Das Datenformat wurde nicht umgestellt. {_cause(exc)}") from exc


def _migrate(conn, path, migrations, target: int, backup_dir, do_backup: bool) -> None:
    """Führt ausstehende Schritte aus. Jeder Schritt liest die Version unter der Sperre neu,
    damit eine zweite Instanz keinen Schritt doppelt ausführt."""
    version = _lock(conn, target)
    if version >= target:
        conn.rollback()  # eine andere Instanz hat schon umgestellt: keine Sicherung nötig
        return
    name = None
    if do_backup:
        try:
            # Sperre bleibt gehalten: niemand schreibt, die Kopie liest einen festen Stand
            name = _pre_migration_backup(conn, path, backup_dir)
        except BaseException:
            conn.rollback()
            raise
    step = version + 1
    while step <= target:
        if not conn.in_transaction:
            version = _lock(conn, target)
            if version >= step:
                conn.rollback()
                step = version + 1
                continue
        try:
            migrations[step](conn)
            _set_version(conn, step)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            hint = f" Vorher angelegte Sicherung: {name}." if name else ""
            raise MigrationError(
                f"Die Umstellung des Datenbankformats auf Schema {step} ist fehlgeschlagen. "
                f"Die Datenbank bleibt auf Schema {step - 1}.{hint} {_cause(exc)}") from exc
        step += 1


def connect(path, *, migrations=None, backup_dir=None, timeout: float = 5.0,
            on_open=None, backup_before_migration: bool = True) -> sqlite3.Connection:
    """Öffnet die Datenbank, legt sie bei Bedarf an und stellt das Datenformat um.

    on_open(conn) läuft direkt nach dem Öffnen (z. B. Härtung für fremde Dateien).
    backup_before_migration=False: keine Sicherung vor der Umstellung (nur für Kopien, deren
    Original erhalten bleibt, etwa beim Einspielen einer Sicherung).

    Fehler mit Meldung für Nutzer (alle DatabaseOpenError): SchemaTooNewError,
    MigrationError, DatabaseBusyError, InvalidDatabaseError."""
    migrations = MIGRATIONS if migrations is None else migrations
    target = current_schema_version(migrations)
    if sorted(migrations) != list(range(BASELINE_VERSION + 1, target + 1)):
        raise ValueError("Migrationen müssen lückenlos ab Version 2 nummeriert sein.")
    memory = str(path) == ":memory:"
    if not memory:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=timeout)
    try:
        if on_open is not None:
            on_open(conn)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        version = schema_version(conn)
        if version > target:
            raise _too_new(version, target)
        if version >= BASELINE_VERSION and not _has_user_tables(conn):
            raise InvalidDatabaseError(E_INVALID)
        fresh = False
        if version == 0:
            fresh = _stamp_baseline(conn, target)
            version = schema_version(conn)
        if version < target:
            _migrate(conn, path, migrations, target, backup_dir,
                     do_backup=backup_before_migration and not memory and not fresh)
    except sqlite3.OperationalError as exc:
        conn.close()
        text = str(exc).lower()
        if "locked" in text or "busy" in text:
            raise DatabaseBusyError(E_BUSY) from exc
        raise
    except sqlite3.DatabaseError as exc:
        conn.close()
        if "not a database" in str(exc).lower():
            raise InvalidDatabaseError(E_INVALID) from exc
        raise
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
