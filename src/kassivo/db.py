import sqlite3
from datetime import date, datetime
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
  where p.tx_id = new.tx_id and +p.reversal_of is null
    and not exists (select 1 from posting r where r.reversal_of = p.id))
begin select raise(abort, 'Der Umsatz hat schon eine gültige Buchung.'); end;
-- höchstens ein gültiger Eröffnungsbestand je Konto
create trigger posting_one_opening before insert on posting
when new.opening_account_id is not null and new.reversal_of is null and exists (
  select 1 from posting p
  where p.opening_account_id = new.opening_account_id and +p.reversal_of is null
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
    first, last = conn.execute('select min(substr(booking_date, 1, 4)),'
                               ' max(substr(booking_date, 1, 4)) from "transaction"').fetchone()
    start_year = min(int(first), today.year) if first else today.year
    end_year = max(int(last), today.year) if last else today.year
    active = None
    for year in range(start_year, end_year + 1):
        pid = conn.execute(
            "insert into period(name, start_date, end_date) values(?,?,?)",
            (f"Jahr {year}", f"{year}-01-01", f"{year}-12-31")).lastrowid
        if year == today.year:
            active = pid
    conn.execute("insert into settings(key, value) values('active_period', ?)"
                 " on conflict(key) do update set value = excluded.value", (str(active),))


# --- Migration 4 ------------------------------------------------------------------------
#
# Eingefroren wie _LEDGER_DDL_4: eigene Hilfsfunktionen, nicht ledger.py (einzige Ausnahme
# ledger.check als reine Prüfung am Ende). Ablauf und Entscheidungen im Plan, Task 4.

# "transaction" ohne category_id; übrige Spalten, Reihenfolge, unique und Fremdschlüssel wie
# vorher (no_member aus Schema 3 steht am Ende).
_TX_DDL_4 = """create table transaction_new(
  id integer primary key,
  account_id integer not null references account(id),
  booking_date text not null, value_date text,
  amount_cents integer not null, bank_ref text,
  cp_name text, cp_iban text, purpose text,
  dedupe_key text not null,
  member_id integer references member(id),
  batch_id integer references import_batch(id),
  no_member integer not null default 0 check (no_member in (0,1)),
  unique(account_id, dedupe_key))"""
_TX_COLUMNS_4 = ("id, account_id, booking_date, value_date, amount_cents, bank_ref, cp_name,"
                 " cp_iban, purpose, dedupe_key, member_id, batch_id, no_member")
_RULE_FIELDS_4 = ("purpose", "cp_name", "cp_iban")
# Nummernbereiche für neue Konten aus Kategorien; danach weiter ab 5000
_NUMBER_RANGES_4 = {"income": (3500, 3999), "expense": (4500, 4999)}
_OVERFLOW_START_4 = 5000
# fest zugeordnete Kategorien des alten Startplans (Name casefold, Art) -> Kontonummern
_FIXED_CATEGORIES_4 = {("sonstiges", "both"): (3900, 4900)}

_today = date.today  # Tests tauschen das Modulattribut aus


class LedgerMigrationError(Exception):
    """Summenvergleich oder Prüfung nach Migration 4 fehlgeschlagen (ohne Personendaten)."""


def _ref_4(conn, account_id: int) -> str:
    number = conn.execute("select number from ledger_account where id = ?",
                          (account_id,)).fetchone()[0]
    return f"Konto {number}" if number is not None else f"Konto-ID {account_id}"


def _new_account_4(conn, name: str, kind: str, groups: dict) -> int:
    lo, hi = _NUMBER_RANGES_4[kind]
    number = _next_free_number_4(conn, lo)
    if number > hi:
        number = _next_free_number_4(conn, _OVERFLOW_START_4)
    group_id = groups[kind]
    clean = _free_name_4(conn, name.strip()[:90] or "Kategorie", kind, group_id)
    return conn.execute("insert into ledger_account(number, name, kind, group_id)"
                        " values(?,?,?,?)", (number, clean, kind, group_id)).lastrowid


def _category_map(conn, categories) -> dict[int, tuple[int, int]]:
    """Kategorie-ID -> (Konto für Eingänge, Konto für Ausgänge), in der Reihenfolge id."""
    groups = {kind: conn.execute("select id from account_group where kind = ? and name = ?",
                                 (kind, name)).fetchone()[0] for kind, name in START_GROUPS_4}
    start = {(name.casefold(), kind): conn.execute(
        "select id from ledger_account where number = ?", (number,)).fetchone()[0]
        for number, name, kind, _role, _group in START_PLAN_4}
    by_number = {number: start[(name.casefold(), kind)]
                 for number, name, kind, _role, _group in START_PLAN_4}

    def account(name: str, kind: str) -> int:
        found = start.get((name.strip().casefold(), kind))
        return found if found is not None else _new_account_4(conn, name, kind, groups)

    out = {}
    for cat_id, name, kind in categories:
        fixed = _FIXED_CATEGORIES_4.get((name.strip().casefold(), kind))
        if fixed is not None:
            out[cat_id] = (by_number[fixed[0]], by_number[fixed[1]])
        elif kind == "in":
            a = account(name, "income")
            out[cat_id] = (a, a)
        elif kind == "out":
            a = account(name, "expense")
            out[cat_id] = (a, a)
        else:
            out[cat_id] = (account(f"{name.strip()} (Einnahmen)", "income"),
                           account(f"{name.strip()} (Ausgaben)", "expense"))
    return out


def _migrate_rules_4(conn, rules, cat_map, cat_kind) -> None:
    """category_rule -> posting_rule in alter Reihenfolge (priority, id). Regeln, die nie
    greifen konnten (unbekanntes Feld, leerer Text, Obergrenze < 0), entfallen; eine
    negative Untergrenze heißt wie bisher keine Untergrenze."""
    for _rid, cat_id, field, contains, priority, lo, hi in rules:
        if cat_id not in cat_map or field not in _RULE_FIELDS_4 or not contains \
                or len(contains) > 200 or (hi is not None and hi < 0):
            continue
        lo = None if lo is not None and lo < 0 else lo
        income, expense = cat_map[cat_id]
        kind = cat_kind[cat_id]
        targets = [income] if kind == "in" else [expense] if kind == "out" else [income, expense]
        for target in targets:
            conn.execute("insert into posting_rule(account_id, field, contains, priority,"
                         " min_cents, max_cents) values(?,?,?,?,?,?)",
                         (target, field, contains, priority, lo, hi))


def _book_migrated_4(conn, tx: int, bank: int, counter: int, amount: int) -> None:
    """Eine Buchung mit einer Zeile; erst posting, dann Zeile (Siegel posting_row_sealed)."""
    pid = conn.execute("insert into posting(booking_date, tx_id, source, created_at)"
                       " select booking_date, id, 'migration', ? from \"transaction\""
                       " where id = ?", (_now_4(), tx)).lastrowid
    if amount > 0:
        row = (pid, bank, counter, amount)
    else:
        row = (pid, counter, bank, -amount)
    conn.execute("insert into posting_row(posting_id, debit_account_id, credit_account_id,"
                 " cents) values(?,?,?,?)", row)


def _now_4() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _sum_check_4(conn, booked, cat_of, cat_map) -> None:
    """Summenvergleich: erwartet aus den Altdaten (Python), tatsächlich per SQL."""
    expected: dict = {}
    expected_count = 0
    for _tx, bank, counter, amount, year in booked:
        expected_count += 1
        expected[(counter, year)] = expected.get((counter, year), 0) + amount
        expected[(bank, year)] = expected.get((bank, year), 0) - amount
    actual: dict = {}
    for acc_id, year, cents in conn.execute(
            "select x.acc, x.year, sum(x.cents) from ("
            " select r.credit_account_id as acc, substr(p.booking_date, 1, 4) as year,"
            "  r.cents as cents from posting p join posting_row r on r.posting_id = p.id"
            " union all"
            " select r.debit_account_id, substr(p.booking_date, 1, 4), -r.cents"
            "  from posting p join posting_row r on r.posting_id = p.id) x"
            " group by x.acc, x.year"):
        actual[(acc_id, year)] = cents  # Haben minus Soll
    keys = sorted(k for k in set(expected) | set(actual)
                  if expected.get(k, 0) != actual.get(k, 0))
    if keys:
        acc_id, year = keys[0]
        raise LedgerMigrationError(
            f"Summenvergleich: {_ref_4(conn, acc_id)}, Jahr {year}: vorher "
            f"{expected.get(keys[0], 0)} Cent, nachher {actual.get(keys[0], 0)} Cent.")
    count = conn.execute("select count(*) from posting where tx_id is not null").fetchone()[0]
    if count != expected_count:
        raise LedgerMigrationError(
            f"Summenvergleich: {expected_count} Umsätze zu verbuchen, {count} gebucht.")
    # je Kategorie und Jahr: Summe über ihre Konten gleich der früheren Kategoriesumme.
    # Kategorien, die sich Konten teilen (z. B. Beiträge und Mitglied ohne Kategorie),
    # werden gemeinsam verglichen.
    bucket: dict = {}
    for key, accounts in cat_map.items():
        ids = {bucket[a] for a in accounts if a in bucket} | {key}
        target = min(ids, key=str)
        for a, b in list(bucket.items()):
            if b in ids:
                bucket[a] = target
        for a in accounts:
            bucket[a] = target
    before: dict = {}
    for tx_id, _bank, _counter, amount, year in booked:
        k = (bucket[cat_map[cat_of[tx_id]][0]], year)
        before[k] = before.get(k, 0) + amount
    after: dict = {}
    for (acc_id, year), cents in actual.items():
        if acc_id in bucket:
            k = (bucket[acc_id], year)
            after[k] = after.get(k, 0) + cents
    for k in sorted(set(before) | set(after), key=str):
        if before.get(k, 0) != after.get(k, 0):
            raise LedgerMigrationError(
                f"Summenvergleich: Kategorie {k[0]}, Jahr {k[1]}: vorher {before.get(k, 0)}"
                f" Cent, nachher {after.get(k, 0)} Cent.")


def _migrate_to_4(conn: sqlite3.Connection) -> None:
    """Schema 4 (#57): Journal mit doppelter Buchführung ersetzt die Kategorien.

    Läuft mit ausgeschalteten Fremdschlüsseln (Neuaufbau von "transaction"); _migrate prüft
    danach PRAGMA foreign_key_check. Scheitert der Summenvergleich, rollt _migrate zurück."""
    # 1. Altbestand lesen
    categories = [tuple(r) for r in conn.execute(
        "select id, name, kind from category order by id")]
    rules = [tuple(r) for r in conn.execute(
        "select id, category_id, field, contains, priority, min_cents, max_cents"
        " from category_rule order by priority, id")]
    txs = [tuple(r) for r in conn.execute(
        'select id, account_id, booking_date, amount_cents, category_id, member_id'
        ' from "transaction" order by booking_date, id')]
    # 2. "transaction" ohne category_id neu aufbauen (IDs bleiben)
    conn.execute(_TX_DDL_4)
    conn.execute(f"insert into transaction_new({_TX_COLUMNS_4})"
                 f' select {_TX_COLUMNS_4} from "transaction"')
    conn.execute('drop table "transaction"')
    conn.execute('alter table transaction_new rename to "transaction"')
    # 3. Kategorien entfernen
    conn.execute("drop table category_rule")
    conn.execute("drop table category")
    # 4. Buchungstabellen und Startplan
    for stmt in _statements(_LEDGER_DDL_4):
        conn.execute(stmt)
    _seed_ledger_4(conn, _today())
    # 5. Kategorien zu Konten, 6. Regeln
    cat_map = _category_map(conn, categories)
    cat_kind = {cat_id: kind for cat_id, _name, kind in categories}
    _migrate_rules_4(conn, rules, cat_map, cat_kind)
    # 7. Verbuchen
    fees = conn.execute("select id from ledger_account where role = 'fees'").fetchone()[0]
    bank_of = {r[0]: r[1] for r in conn.execute(
        "select bank_account_id, id from ledger_account where bank_account_id is not null")}
    fee_key = "fees"  # Mitglied ohne Kategorie zählt im Vergleich bei "Beiträge"
    cat_map_sum = {**cat_map, fee_key: (fees, fees)}
    booked, cat_of = [], {}
    for tx_id, account_id, day, amount, cat_id, member_id in txs:
        if amount == 0:
            continue
        if cat_id is not None and cat_id in cat_map:
            income, expense = cat_map[cat_id]
            counter = income if amount > 0 else expense
            cat_of[tx_id] = cat_id
        elif cat_id is None and member_id is not None:
            counter = fees
            cat_of[tx_id] = fee_key
        else:
            continue
        bank = bank_of[account_id]
        _book_migrated_4(conn, tx_id, bank, counter, amount)
        booked.append((tx_id, bank, counter, amount, day[:4]))
    # 8. Summenvergleich, 9. Prüfung
    _sum_check_4(conn, booked, cat_of, cat_map_sum)
    from kassivo import ledger  # spät: ledger importiert periods, periods importiert db
    findings = ledger.check(conn)
    if findings:
        raise LedgerMigrationError("Prüfung: " + " ".join(findings[:3]))


_migrate_to_4.fk_off = True


BASELINE_VERSION = 1
SCHEMA_VERSION = 4
MIGRATIONS: dict = {2: _migrate_to_2, 3: _migrate_to_3, 4: _migrate_to_4}


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
