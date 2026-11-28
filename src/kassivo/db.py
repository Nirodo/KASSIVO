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
#   5. Tabellen neu aufbauen nur in einem Schritt mit `fk_off = True`.
#      (_migrate_to_N.fk_off = True). PRAGMA foreign_keys wirkt nicht in einer
#      Transaktion; _migrate schaltet die Prüfung deshalb vor begin immediate aus,
#      prüft danach mit PRAGMA foreign_key_check und schaltet sie immer wieder ein.
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


def _migrate_to_3(conn: sqlite3.Connection) -> None:
    """Schema 3 (#6): transaction.no_member, der Nutzer hat „— keins —“ gewählt.

    1 = bewusst ohne Mitglied: die automatische Zuordnung (Import, „Unklare
    automatisch zuordnen“, „Absender merken“) lässt die Buchung aus. Wie
    category_id und member_id eine Anmerkung, keine Änderung der Buchung.
    Bestehende Buchungen: 0 (wer früher „— keins —“ gewählt hat, ist nicht bekannt).
    """
    conn.execute('alter table "transaction" add column no_member integer not null default 0'
                 " check (no_member in (0,1))")


# --- Schema 4: Buchhaltung (Konten, Kostenstellen, Perioden, Buchungen) -----------------
#
# Eingefroren ab Freigabe wie SCHEMA. Migration 4 nutzt diese Texte und eigene
# Hilfsfunktionen hier, nicht ledger.py (das Modul darf sich später ändern).
# Entscheidungen E2 bis E15: docs/superpowers/plans/2026-11-28-buchhaltung-schritt2-kern.md

_LEDGER_DDL_4 = """
-- Kontogruppe wie bei Webling (E12): gehört zu einer Art, Name je Art eindeutig
create table account_group(
  id integer primary key,
  kind text not null check(kind in ('asset','liability','income','expense')),
  name text not null check(length(trim(name)) between 1 and 100));
create unique index account_group_name on account_group(kind, name collate nocase);
-- Nummer optional, wenn gesetzt eindeutig; Name je (Art, Gruppe) eindeutig (E13)
create table ledger_account(
  id integer primary key,
  number integer unique
    check(number is null or (typeof(number) = 'integer' and number between 1 and 99999)),
  name text not null check(length(trim(name)) between 1 and 100),
  kind text not null check(kind in ('asset','liability','income','expense')),
  group_id integer references account_group(id),
  hidden integer not null default 0 check(hidden in (0,1)),
  role text unique check(role in ('bank_default','fees','equity')),
  bank_account_id integer unique references account(id),
  check(bank_account_id is null or kind = 'asset'),
  check(role is null or hidden = 0));
create table cost_center(
  id integer primary key,
  name text not null unique check(length(trim(name)) between 1 and 100),
  hidden integer not null default 0 check(hidden in (0,1)));
-- Buchungsperiode wie bei Webling: frei benennbar, Von-Bis, offen oder abgeschlossen
create table period(
  id integer primary key,
  name text not null unique check(length(trim(name)) between 1 and 60),
  start_date text not null check(start_date glob '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  end_date text not null check(end_date glob '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  status text not null default 'open' check(status in ('open','closed')),
  closed_at text,
  check(start_date <= end_date),
  check((status = 'closed') = (closed_at is not null)));
create table posting(
  id integer primary key,
  booking_date text not null
    check(booking_date glob '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  text text check(text is null or length(text) <= 200),
  voucher text check(voucher is null or length(voucher) <= 40),
  tx_id integer references "transaction"(id),
  -- Eröffnungsbestand eines Kontos (höchstens einer gültig je Konto)
  opening_account_id integer references ledger_account(id),
  reversal_of integer unique references posting(id),
  source text not null check(source in
    ('migration','import','rule','fee','manual','opening','closing','carryover')),
  created_at text not null,
  check(tx_id is null or opening_account_id is null),
  check((source = 'opening') = (opening_account_id is not null)));
create table posting_row(
  id integer primary key,
  posting_id integer not null references posting(id),
  debit_account_id integer not null references ledger_account(id),
  credit_account_id integer not null references ledger_account(id),
  cents integer not null
    check(typeof(cents) = 'integer' and cents between 1 and 100000000000),
  cost_center_id integer references cost_center(id),
  check(debit_account_id <> credit_account_id));
create table posting_rule(
  id integer primary key,
  account_id integer not null references ledger_account(id),
  field text not null check(field in ('purpose','cp_name','cp_iban')),
  contains text not null check(length(contains) between 1 and 200),
  priority integer not null default 100,
  min_cents integer check(min_cents is null or (typeof(min_cents) = 'integer' and min_cents >= 0)),
  max_cents integer check(max_cents is null or (typeof(max_cents) = 'integer' and max_cents >= 0)));
create index posting_tx on posting(tx_id);
create index posting_opening on posting(opening_account_id);
create index posting_date on posting(booking_date);
create index posting_row_posting on posting_row(posting_id);
create index posting_row_debit on posting_row(debit_account_id);
create index posting_row_credit on posting_row(credit_account_id);
create index posting_rule_account on posting_rule(account_id);
create index period_start on period(start_date);
create unique index ledger_account_name
  on ledger_account(kind, ifnull(group_id, 0), name collate nocase);
create index ledger_account_group on ledger_account(group_id);

create trigger ledger_account_group_kind before insert on ledger_account
when new.group_id is not null
 and (select kind from account_group where id = new.group_id) is not new.kind
begin select raise(abort, 'Die Kontogruppe gehört zu einer anderen Kontoart.'); end;
create trigger ledger_account_group_kind_upd before update of kind, group_id on ledger_account
when new.group_id is not null
 and (select kind from account_group where id = new.group_id) is not new.kind
begin select raise(abort, 'Die Kontogruppe gehört zu einer anderen Kontoart.'); end;
create trigger account_group_kind_fixed before update of kind on account_group
when new.kind <> old.kind and exists (select 1 from ledger_account where group_id = old.id)
begin select raise(abort, 'Die Art einer benutzten Kontogruppe lässt sich nicht ändern.'); end;
create trigger posting_no_update before update on posting
begin select raise(abort, 'Buchungen sind unveränderlich (Storno statt Ändern).'); end;
create trigger posting_no_delete before delete on posting
begin select raise(abort, 'Buchungen werden nicht gelöscht (Storno statt Löschen).'); end;
create trigger posting_row_no_update before update on posting_row
begin select raise(abort, 'Buchungen sind unveränderlich (Storno statt Ändern).'); end;
create trigger posting_row_no_delete before delete on posting_row
begin select raise(abort, 'Buchungen werden nicht gelöscht (Storno statt Löschen).'); end;
-- Zeilen nur zur neuesten Buchung: ältere Buchungen sind geschlossen
create trigger posting_row_sealed before insert on posting_row
when new.posting_id is not (select max(id) from posting)
begin select raise(abort, 'Die Buchung ist abgeschlossen.'); end;
create trigger posting_row_cost_center before insert on posting_row
when new.cost_center_id is not null and not exists (
  select 1 from ledger_account a
  where a.id in (new.debit_account_id, new.credit_account_id)
    and a.kind in ('income','expense'))
begin select raise(abort, 'Kostenstellen gibt es nur bei Ertrags- und Aufwandskonten.'); end;
-- jede Buchung liegt in einer offenen Periode (auch Storno)
create trigger posting_in_open_period before insert on posting
when not exists (select 1 from period
                 where new.booking_date between start_date and end_date and status = 'open')
begin select raise(abort, 'Für dieses Datum gibt es keine offene Buchungsperiode.'); end;
-- höchstens eine gültige Buchung je Bankumsatz
create trigger posting_one_valid before insert on posting
when new.tx_id is not null and new.reversal_of is null and exists (
  select 1 from posting p
  where p.tx_id = new.tx_id and p.reversal_of is null
    and not exists (select 1 from posting r where r.reversal_of = p.id))
begin select raise(abort, 'Der Umsatz hat schon eine gültige Buchung.'); end;
-- höchstens ein gültiger Eröffnungsbestand je Konto
create trigger posting_one_opening before insert on posting
when new.opening_account_id is not null and new.reversal_of is null and exists (
  select 1 from posting p
  where p.opening_account_id = new.opening_account_id and p.reversal_of is null
    and not exists (select 1 from posting r where r.reversal_of = p.id))
begin select raise(abort, 'Das Konto hat schon einen Eröffnungsbestand.'); end;
create trigger posting_reversal_ok before insert on posting
when new.reversal_of is not null
begin
  select raise(abort, 'Storno ohne stornierte Buchung.')
    where not exists (select 1 from posting where id = new.reversal_of);
  select raise(abort, 'Ein Storno kann nicht storniert werden.')
    where (select reversal_of from posting where id = new.reversal_of) is not null;
  select raise(abort, 'Storno gehört zu einem anderen Umsatz oder Konto.')
    where new.tx_id is not (select tx_id from posting where id = new.reversal_of)
       or new.opening_account_id is not (select opening_account_id from posting
                                          where id = new.reversal_of);
end;
create trigger period_no_overlap before insert on period
when exists (select 1 from period
             where new.start_date <= end_date and new.end_date >= start_date)
begin select raise(abort, 'Buchungsperioden dürfen sich nicht überschneiden.'); end;
create trigger period_no_overlap_upd before update of start_date, end_date on period
when exists (select 1 from period where id <> new.id
             and new.start_date <= end_date and new.end_date >= start_date)
begin select raise(abort, 'Buchungsperioden dürfen sich nicht überschneiden.'); end;
-- Grenzen nur ändern, wenn keine Buchung dadurch aus der Periode fällt
create trigger period_keep_postings before update of start_date, end_date on period
when exists (select 1 from posting
             where booking_date between old.start_date and old.end_date
               and booking_date not between new.start_date and new.end_date)
begin select raise(abort, 'Die Periode enthält Buchungen außerhalb der neuen Grenzen.'); end;
create trigger period_closed_fixed before update of start_date, end_date on period
when old.status = 'closed'
begin select raise(abort, 'Eine abgeschlossene Periode lässt sich nicht ändern.'); end;
create trigger period_keep before delete on period
when exists (select 1 from posting
             where booking_date between old.start_date and old.end_date)
begin select raise(abort, 'Die Periode enthält Buchungen.'); end;
create trigger ledger_account_kind_fixed before update of kind on ledger_account
when new.kind <> old.kind and (
  exists (select 1 from posting_row
          where debit_account_id = old.id or credit_account_id = old.id)
  or exists (select 1 from posting_rule where account_id = old.id))
begin select raise(abort, 'Die Art eines benutzten Kontos lässt sich nicht ändern.'); end;
create trigger ledger_account_bank_fixed before update of bank_account_id on ledger_account
when old.bank_account_id is not null and new.bank_account_id is not old.bank_account_id
begin select raise(abort, 'Die Zuordnung zum Bankkonto lässt sich nicht ändern.'); end;
create trigger ledger_account_keep before delete on ledger_account
when old.role is not null or old.bank_account_id is not null
begin select raise(abort, 'Dieses Konto braucht KASSIVO.'); end;
create trigger posting_rule_target before insert on posting_rule
when (select kind from ledger_account where id = new.account_id) not in ('income','expense')
begin select raise(abort, 'Regeln buchen nur auf Ertrags- oder Aufwandskonten.'); end;
create trigger posting_rule_target_upd before update of account_id on posting_rule
when (select kind from ledger_account where id = new.account_id) not in ('income','expense')
begin select raise(abort, 'Regeln buchen nur auf Ertrags- oder Aufwandskonten.'); end;
"""

# Startgruppen (E12) und generischer Startplan (Nummer, Name, Art, Rolle, Gruppe).
# Keine vereinsspezifischen Namen. KASSIVO erkennt eigene Konten nur über die Rolle.
START_GROUPS_4 = (
    ("asset", "Umlaufvermögen"),
    ("liability", "Eigenkapital"),
    ("income", "Ertrag"),
    ("expense", "Ausgaben"),
)
START_PLAN_4 = (
    (1000, "Barkasse", "asset", None, "Umlaufvermögen"),
    (1200, "Bank", "asset", "bank_default", "Umlaufvermögen"),
    (2000, "Vereinsvermögen", "liability", "equity", "Eigenkapital"),
    (3000, "Beiträge", "income", "fees", "Ertrag"),
    (3100, "Spenden", "income", None, "Ertrag"),
    (3200, "Zinsen", "income", None, "Ertrag"),
    (3900, "Sonstige Einnahmen", "income", None, "Ertrag"),
    (4000, "Hauptverein", "expense", None, "Ausgaben"),
    (4100, "Bankgebühren", "expense", None, "Ausgaben"),
    (4900, "Sonstige Ausgaben", "expense", None, "Ausgaben"),
)
MAX_ACCOUNT_NUMBER_4 = 99999


def _next_free_number_4(conn: sqlite3.Connection, start: int) -> int:
    """Kleinste freie Kontonummer ab start (eingefroren für Migration 4)."""
    taken = {r[0] for r in conn.execute(
        "select number from ledger_account where number >= ?", (start,)).fetchall()}
    n = start
    while n in taken:
        n += 1
    if n > MAX_ACCOUNT_NUMBER_4:
        raise ValueError("Keine freie Kontonummer mehr.")
    return n


def _free_name_4(conn: sqlite3.Connection, name: str, kind: str, group_id) -> str:
    """name, sonst "name (2)", "name (3)" ... frei in (Art, Gruppe), ohne Groß/Klein (E13)."""
    taken = {r[0].casefold() for r in conn.execute(
        "select name from ledger_account where kind = ? and ifnull(group_id, 0) = ifnull(?, 0)",
        (kind, group_id)).fetchall()}
    candidate, n = name, 1
    while candidate.casefold() in taken:
        n += 1
        candidate = f"{name} ({n})"
    return candidate


def _seed_ledger_4(conn: sqlite3.Connection, today) -> None:
    """Startgruppen, Startplan, Bankkonten verknüpfen, Kalenderjahre als Perioden (E15).

    Kein commit(): läuft in der Transaktion des Aufrufers (Migration 4, Tests)."""
    from kassivo.iban import mask_iban
    groups = {}
    for kind, name in START_GROUPS_4:
        groups[(kind, name)] = conn.execute(
            "insert into account_group(kind, name) values(?,?)", (kind, name)).lastrowid
    for number, name, kind, role, group in START_PLAN_4:
        conn.execute("insert into ledger_account(number, name, kind, role, group_id)"
                     " values(?,?,?,?,?)", (number, name, kind, role, groups[(kind, group)]))
    default = conn.execute("select id, group_id from ledger_account"
                           " where role = 'bank_default'").fetchone()
    for i, bank in enumerate(conn.execute("select id, iban from account order by id").fetchall()):
        if i == 0:
            conn.execute("update ledger_account set bank_account_id = ? where id = ?",
                         (bank[0], default[0]))
            continue
        name = _free_name_4(conn, f"Bank {mask_iban(bank[1])}", "asset", default[1])
        conn.execute("insert into ledger_account(number, name, kind, group_id, bank_account_id)"
                     " values(?,?,?,?,?)",
                     (_next_free_number_4(conn, 1201), name, "asset", default[1], bank[0]))
    first = conn.execute('select min(substr(booking_date, 1, 4)) from "transaction"'
                         ).fetchone()[0]
    start_year = min(int(first), today.year) if first else today.year
    active = None
    for year in range(start_year, today.year + 1):
        pid = conn.execute(
            "insert into period(name, start_date, end_date) values(?,?,?)",
            (f"Jahr {year}", f"{year}-01-01", f"{year}-12-31")).lastrowid
        if year == today.year:
            active = pid
    conn.execute("insert into settings(key, value) values('active_period', ?)"
                 " on conflict(key) do update set value = excluded.value", (str(active),))


BASELINE_VERSION = 1
SCHEMA_VERSION = 3
MIGRATIONS: dict = {2: _migrate_to_2, 3: _migrate_to_3}


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
    # Ziel ist die höchste bekannte Migration; für MIGRATIONS gleich SCHEMA_VERSION
    # (Test test_registry_matches_constant). Injizierte Migrationen (Tests) dürfen
    # damit unter SCHEMA_VERSION enden, ohne als lückenhaft zu gelten.
    return max(migrations, default=BASELINE_VERSION)


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


class _FkViolation(Exception):
    """Ein fk_off-Schritt hat verwaiste Verweise hinterlassen (Meldung nur mit Tabellennamen)."""


def _fk_check(conn: sqlite3.Connection) -> list[str]:
    """Tabellen mit verletzten Fremdschlüsseln (PRAGMA foreign_key_check), ohne Werte."""
    return sorted({r[0] for r in conn.execute("pragma foreign_key_check").fetchall()})


def _fk_disable(conn: sqlite3.Connection) -> None:
    """Fremdschlüsselprüfung für einen fk_off-Schritt ausschalten (nur außerhalb einer
    Transaktion wirksam). Eine offene Transaktion (Sicherung unter Sperre) hat nichts
    geschrieben und wird beendet; _lock liest die Version danach unter Sperre neu."""
    if conn.in_transaction:
        conn.rollback()
    conn.execute("PRAGMA foreign_keys = OFF")
    if conn.execute("PRAGMA foreign_keys").fetchall()[0][0] != 0:
        raise MigrationError(
            "Die Fremdschlüsselprüfung ließ sich für die Umstellung nicht ausschalten. "
            "Das Datenformat wurde nicht umgestellt.")


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
        func = migrations[step]
        fk_off = getattr(func, "fk_off", False) is True
        try:
            if fk_off:
                _fk_disable(conn)
            if not conn.in_transaction:
                version = _lock(conn, target)
                if version >= step:
                    conn.rollback()
                    step = version + 1
                    continue
            try:
                func(conn)
                if fk_off:
                    bad = _fk_check(conn)
                    if bad:
                        raise _FkViolation(
                            "Fremdschlüssel verletzt in: " + ", ".join(bad) + ".")
                _set_version(conn, step)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                hint = f" Vorher angelegte Sicherung: {name}." if name else ""
                raise MigrationError(
                    f"Die Umstellung des Datenbankformats auf Schema {step} ist fehlgeschlagen. "
                    f"Die Datenbank bleibt auf Schema {step - 1}.{hint} {_cause(exc)}") from exc
        finally:
            if fk_off:
                if conn.in_transaction:
                    conn.rollback()
                conn.execute("PRAGMA foreign_keys = ON")
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
        if version < 0:  # KASSIVO schreibt nie eine negative Version
            raise InvalidDatabaseError(E_INVALID)
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
