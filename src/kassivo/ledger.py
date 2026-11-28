"""Buchungskern: Kontogruppen, Konten, Kostenstellen, Buchungen, Storno, Prüfung.

Doppelte Buchführung mit Buchungssatz-Zeilen (E2): jede Zeile hat Soll-Konto, Haben-Konto
und einen Betrag > 0 in Cent. Buchungen werden nie geändert oder gelöscht; Korrektur heißt
Storno plus neue Buchung (E3). Keine Funktion ruft commit(), die Aufrufer committen.
Meldungen sind deutsch und nennen nie IBAN, Namen oder Verwendungszweck.
"""
import sqlite3
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime

from kassivo import periods
from kassivo.iban import mask_iban

KINDS = ("asset", "liability", "income", "expense")
KIND_TEXT = {"asset": "Aktiv", "liability": "Passiv", "income": "Ertrag", "expense": "Aufwand"}
SOURCES = ("migration", "import", "rule", "fee", "manual", "opening", "closing", "carryover")
# bis Schritt 3 Anzeigename für Umsätze ohne Buchung; darf kein Konto heißen
RESERVED_NAME = "Ohne Kategorie"
MAX_NAME = 100
MAX_NUMBER = 99999

# Sortierung nach Art in KINDS-Reihenfolge (fester Text, nie aus Eingaben gebaut)
_KIND_ORDER = ("case a.kind when 'asset' then 0 when 'liability' then 1"
               " when 'income' then 2 else 3 end")


class LedgerError(ValueError):
    """Meldung für Nutzer (deutsch, ohne Personendaten)."""


def _clean_name(name: str, what: str = "Der Name") -> str:
    clean = (name or "").strip() if isinstance(name, str) else ""
    if not 1 <= len(clean) <= MAX_NAME:
        raise LedgerError(f"{what} muss 1 bis {MAX_NAME} Zeichen haben.")
    return clean


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise LedgerError("Unbekannte Kontoart.")
    return kind


# --- Kontogruppen (E12) ------------------------------------------------------------

def _group_row(conn, group_id: int) -> sqlite3.Row:
    row = conn.execute("select id, kind, name from account_group where id = ?",
                       (group_id,)).fetchone()
    if row is None:
        raise LedgerError("Die Kontogruppe gibt es nicht (mehr).")
    return row


def find_group(conn, kind: str, name: str) -> int | None:
    """Gruppe über (Art, Name) ohne Groß/Klein; für den späteren CSV-Import (E14)."""
    key = (name or "").strip().casefold()
    for r in conn.execute("select id, name from account_group where kind = ?", (kind,)):
        if r["name"].casefold() == key:
            return r["id"]
    return None


def _check_group_name(conn, kind: str, name: str, own_id: int | None = None) -> str:
    clean = _clean_name(name, "Der Gruppenname")
    other = find_group(conn, kind, clean)
    if other is not None and other != own_id:
        raise LedgerError("Diese Kontogruppe gibt es in dieser Kontoart schon.")
    return clean


def create_group(conn, kind: str, name: str) -> int:
    clean = _check_group_name(conn, _kind(kind), name)
    return conn.execute("insert into account_group(kind, name) values(?,?)",
                        (kind, clean)).lastrowid


def rename_group(conn, group_id: int, name: str) -> None:
    row = _group_row(conn, group_id)
    clean = _check_group_name(conn, row["kind"], name, group_id)
    conn.execute("update account_group set name = ? where id = ?", (clean, group_id))


def delete_group(conn, group_id: int) -> None:
    _group_row(conn, group_id)
    if conn.execute("select 1 from ledger_account where group_id = ?", (group_id,)).fetchone():
        raise LedgerError("Die Kontogruppe enthält Konten und lässt sich nicht löschen.")
    conn.execute("delete from account_group where id = ?", (group_id,))


def list_groups(conn, kind: str | None = None) -> list[sqlite3.Row]:
    sql = ("select a.id, a.kind, a.name,"
           " (select count(*) from ledger_account l where l.group_id = a.id) as accounts"
           " from account_group a")
    args: tuple = ()
    if kind is not None:
        sql += " where a.kind = ?"
        args = (kind,)
    return conn.execute(sql + f" order by {_KIND_ORDER}, a.name collate nocase, a.id",
                        args).fetchall()


# --- Konten ------------------------------------------------------------------------

_ACCOUNT_COLUMNS = (
    "select a.id, a.number, a.name, a.kind, a.group_id, g.name as group_name, a.hidden,"
    " a.role, a.bank_account_id,"
    " (select count(*) from posting_row r where r.debit_account_id = a.id"
    "  or r.credit_account_id = a.id)"
    " + (select count(*) from posting_rule u where u.account_id = a.id) as used"
    " from ledger_account a left join account_group g on g.id = a.group_id")


def _account_row(conn, account_id: int) -> sqlite3.Row:
    row = conn.execute(_ACCOUNT_COLUMNS + " where a.id = ?", (account_id,)).fetchone()
    if row is None:
        raise LedgerError("Das Konto gibt es nicht (mehr).")
    return row


def account_label(row) -> str:
    """Anzeige "1200 Bank", ohne Nummer nur der Name."""
    return f"{row['number']} {row['name']}" if row["number"] is not None else row["name"]


def ref(number, account_id) -> str:
    """Konto in Meldungen: Nummer, ohne Nummer die ID (nie der Name)."""
    return f"Konto {number}" if number is not None else f"Konto-ID {account_id}"


def _check_number(conn, number, own_id: int | None = None):
    if number is None:
        return None
    if type(number) is not int or not 1 <= number <= MAX_NUMBER:
        raise LedgerError(f"Die Kontonummer muss eine ganze Zahl von 1 bis {MAX_NUMBER} sein.")
    other = conn.execute("select id from ledger_account where number = ?", (number,)).fetchone()
    if other is not None and other[0] != own_id:
        raise LedgerError("Die Kontonummer ist schon vergeben.")
    return number


def _check_account_name(conn, name: str, kind: str, group_id, own_id: int | None = None) -> str:
    clean = _clean_name(name)
    key = clean.casefold()
    if key == RESERVED_NAME.casefold():
        raise LedgerError(f"Der Name „{RESERVED_NAME}“ ist reserviert.")
    for r in conn.execute("select id, name from ledger_account where kind = ?"
                          " and ifnull(group_id, 0) = ifnull(?, 0)", (kind, group_id)):
        if r["id"] != own_id and r["name"].casefold() == key:
            raise LedgerError("Ein Konto mit diesem Namen gibt es in dieser Gruppe schon.")
    return clean


def _check_group(conn, group_id, kind: str):
    if group_id is None:
        return None
    if _group_row(conn, group_id)["kind"] != kind:
        raise LedgerError("Die Kontogruppe gehört zu einer anderen Kontoart.")
    return group_id


def create_account(conn, name: str, kind: str, *, number: int | None = None,
                   group_id: int | None = None) -> int:
    _kind(kind)
    number = _check_number(conn, number)
    group_id = _check_group(conn, group_id, kind)
    clean = _check_account_name(conn, name, kind, group_id)
    return conn.execute(
        "insert into ledger_account(number, name, kind, group_id) values(?,?,?,?)",
        (number, clean, kind, group_id)).lastrowid


def update_account(conn, account_id: int, *, name: str, number: int | None,
                   group_id: int | None) -> None:
    row = _account_row(conn, account_id)
    number = _check_number(conn, number, account_id)
    group_id = _check_group(conn, group_id, row["kind"])
    clean = _check_account_name(conn, name, row["kind"], group_id, account_id)
    conn.execute("update ledger_account set number = ?, name = ?, group_id = ? where id = ?",
                 (number, clean, group_id, account_id))


def set_account_hidden(conn, account_id: int, hidden: bool) -> None:
    row = _account_row(conn, account_id)
    if hidden and (row["role"] is not None or row["bank_account_id"] is not None):
        raise LedgerError("Dieses Konto braucht KASSIVO; es lässt sich nicht ausblenden.")
    conn.execute("update ledger_account set hidden = ? where id = ?",
                 (1 if hidden else 0, account_id))


def delete_account(conn, account_id: int) -> None:
    row = _account_row(conn, account_id)
    if row["role"] is not None or row["bank_account_id"] is not None:
        raise LedgerError("Dieses Konto braucht KASSIVO; es lässt sich nicht löschen.")
    if row["used"] or conn.execute("select 1 from posting where opening_account_id = ?",
                                   (account_id,)).fetchone():
        raise LedgerError("Das Konto ist benutzt und lässt sich nur ausblenden.")
    conn.execute("delete from ledger_account where id = ?", (account_id,))


def list_accounts(conn, *, visible_only: bool = False,
                  kinds: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
    """Nach Art, Gruppe (ohne Gruppe zuletzt), Nummer (ohne Nummer zuletzt), Name."""
    where, args = [], []
    if visible_only:
        where.append("a.hidden = 0")
    if kinds is not None:
        where.append("a.kind in (" + ",".join("?" * len(kinds)) + ")")
        args.extend(kinds)
    sql = _ACCOUNT_COLUMNS + (" where " + " and ".join(where) if where else "")
    sql += (f" order by {_KIND_ORDER}, a.group_id is null, g.name collate nocase, a.group_id,"
            " a.number is null, a.number, a.name collate nocase, a.id")
    return conn.execute(sql, args).fetchall()


def next_free_number(conn, start: int) -> int:
    taken = {r[0] for r in conn.execute(
        "select number from ledger_account where number >= ?", (start,)).fetchall()}
    n = start
    while n in taken:
        n += 1
    if n > MAX_NUMBER:
        raise LedgerError("Es gibt keine freie Kontonummer mehr.")
    return n


def _role_account(conn, role: str) -> int:
    row = conn.execute("select id from ledger_account where role = ?", (role,)).fetchone()
    if row is None:
        raise LedgerError("Ein von KASSIVO benötigtes Konto fehlt.")
    return row[0]


def fees_account_id(conn) -> int:
    return _role_account(conn, "fees")


def equity_account_id(conn) -> int:
    return _role_account(conn, "equity")


def bank_ledger_account(conn, bank_account_id: int) -> int:
    """Aktivkonto eines Bankkontos: vorhandenes, sonst das Startkonto "Bank" verknüpfen,
    sonst neu "Bank DE12…1234" in der Gruppe von "Bank" (wie im Seed)."""
    found = conn.execute("select id from ledger_account where bank_account_id = ?",
                         (bank_account_id,)).fetchone()
    if found is not None:
        return found[0]
    bank = conn.execute("select iban from account where id = ?", (bank_account_id,)).fetchone()
    if bank is None:
        raise LedgerError("Das Bankkonto gibt es nicht.")
    default = conn.execute("select id, group_id, bank_account_id from ledger_account"
                           " where role = 'bank_default'").fetchone()
    if default is not None and default["bank_account_id"] is None:
        conn.execute("update ledger_account set bank_account_id = ? where id = ?",
                     (bank_account_id, default["id"]))
        return default["id"]
    group_id = default["group_id"] if default is not None else None
    base = f"Bank {mask_iban(bank['iban'])}"
    taken = {r[0].casefold() for r in conn.execute(
        "select name from ledger_account where kind = 'asset'"
        " and ifnull(group_id, 0) = ifnull(?, 0)", (group_id,))}
    name, n = base, 1
    while name.casefold() in taken:
        n += 1
        name = f"{base} ({n})"
    return conn.execute(
        "insert into ledger_account(number, name, kind, group_id, bank_account_id)"
        " values(?,?,?,?,?)",
        (next_free_number(conn, 1201), name, "asset", group_id, bank_account_id)).lastrowid


# --- Kostenstellen -----------------------------------------------------------------

def _cc_row(conn, cc_id: int) -> sqlite3.Row:
    row = conn.execute(
        "select c.id, c.name, c.hidden,"
        " (select count(*) from posting_row r where r.cost_center_id = c.id) as used"
        " from cost_center c where c.id = ?", (cc_id,)).fetchone()
    if row is None:
        raise LedgerError("Die Kostenstelle gibt es nicht (mehr).")
    return row


def _check_cc_name(conn, name: str, own_id: int | None = None) -> str:
    clean = _clean_name(name)
    for r in conn.execute("select id, name from cost_center"):
        if r["id"] != own_id and r["name"].casefold() == clean.casefold():
            raise LedgerError("Eine Kostenstelle mit diesem Namen gibt es schon.")
    return clean


def create_cost_center(conn, name: str) -> int:
    return conn.execute("insert into cost_center(name) values(?)",
                        (_check_cc_name(conn, name),)).lastrowid


def rename_cost_center(conn, cc_id: int, name: str) -> None:
    _cc_row(conn, cc_id)
    conn.execute("update cost_center set name = ? where id = ?",
                 (_check_cc_name(conn, name, cc_id), cc_id))


def set_cost_center_hidden(conn, cc_id: int, hidden: bool) -> None:
    _cc_row(conn, cc_id)
    conn.execute("update cost_center set hidden = ? where id = ?", (1 if hidden else 0, cc_id))


def delete_cost_center(conn, cc_id: int) -> None:
    if _cc_row(conn, cc_id)["used"]:
        raise LedgerError("Die Kostenstelle ist benutzt und lässt sich nur ausblenden.")
    conn.execute("delete from cost_center where id = ?", (cc_id,))


def list_cost_centers(conn, *, visible_only: bool = False) -> list[sqlite3.Row]:
    sql = ("select c.id, c.name, c.hidden,"
           " (select count(*) from posting_row r where r.cost_center_id = c.id) as used"
           " from cost_center c")
    if visible_only:
        sql += " where c.hidden = 0"
    return conn.execute(sql + " order by c.name collate nocase, c.id").fetchall()


# --- Buchen ------------------------------------------------------------------------

MAX_ROW_CENTS = 100_000_000_000  # wie CHECK in posting_row


@dataclass(frozen=True)
class Part:
    """Teil eines Umsatzes. cents mit dem Vorzeichen aus Sicht der Bank (Eingang positiv)."""
    account_id: int
    cents: int
    cost_center_id: int | None = None


def _is_int(value) -> bool:
    return type(value) is int  # bool und float sind keine Cent-Beträge


@contextmanager
def _atomic(conn):
    """Savepoint: bei Fehler bleibt nichts von diesem Schritt übrig, ohne commit().
    Ohne offene Transaktion wird erst eine begonnen; sonst würde release committen."""
    if not conn.in_transaction:
        conn.execute("begin")
    conn.execute("savepoint ledger_step")
    try:
        yield
    except BaseException:
        conn.execute("rollback to ledger_step")
        conn.execute("release ledger_step")
        raise
    conn.execute("release ledger_step")


def _db_error(exc: sqlite3.IntegrityError) -> LedgerError:
    """Trigger-Meldungen sind deutsch und ohne Personendaten; sonst eine feste Meldung."""
    text = str(exc)
    if text and not any(word in text for word in ("constraint", "FOREIGN KEY", "UNIQUE")):
        return LedgerError(text)
    return LedgerError("Die Buchung verletzt eine Regel der Datenbank.")


def _check_day(day: str) -> str:
    try:
        return date.fromisoformat(day).isoformat()
    except (TypeError, ValueError):
        raise LedgerError("Ungültiges Buchungsdatum.") from None


def _bookable(conn, account_id) -> sqlite3.Row:
    row = conn.execute("select id, kind, hidden from ledger_account where id = ?",
                       (account_id,)).fetchone()
    if row is None:
        raise LedgerError("Das Konto gibt es nicht (mehr).")
    if row["hidden"]:
        raise LedgerError("Auf ein ausgeblendetes Konto wird nicht neu gebucht.")
    return row


def _check_rows(conn, rows) -> None:
    for debit, credit, cents, cc in rows:
        kinds = {_bookable(conn, debit)["kind"], _bookable(conn, credit)["kind"]}
        if debit == credit:
            raise LedgerError("Soll und Haben müssen verschiedene Konten sein.")
        if not _is_int(cents) or not 1 <= cents <= MAX_ROW_CENTS:
            raise LedgerError("Der Betrag einer Zeile muss eine positive ganze Zahl in Cent sein.")
        if cc is not None:
            row = conn.execute("select hidden from cost_center where id = ?", (cc,)).fetchone()
            if row is None or row["hidden"]:
                raise LedgerError("Die Kostenstelle gibt es nicht oder sie ist ausgeblendet.")
            if not kinds & {"income", "expense"}:
                raise LedgerError("Kostenstellen gibt es nur bei Ertrags- und Aufwandskonten.")


def _insert(conn, day: str, rows, *, source: str, tx_id=None, opening_account_id=None,
            reversal_of=None, text=None, voucher=None) -> int:
    with _atomic(conn):
        try:
            pid = conn.execute(
                "insert into posting(booking_date, text, voucher, tx_id, opening_account_id,"
                " reversal_of, source, created_at) values(?,?,?,?,?,?,?,?)",
                (day, text, voucher, tx_id, opening_account_id, reversal_of, source,
                 datetime.now().replace(microsecond=0).isoformat())).lastrowid
            for debit, credit, cents, cc in rows:
                conn.execute(
                    "insert into posting_row(posting_id, debit_account_id, credit_account_id,"
                    " cents, cost_center_id) values(?,?,?,?,?)", (pid, debit, credit, cents, cc))
        except sqlite3.IntegrityError as exc:
            raise _db_error(exc) from None
    return pid


def _check_source(source: str) -> None:
    if source not in SOURCES:
        raise LedgerError("Unbekannte Herkunft der Buchung.")


def post(conn, booking_date: str, rows: list[tuple[int, int, int, int | None]], *,
         source: str, tx_id: int | None = None, opening_account_id: int | None = None,
         text: str | None = None, voucher: str | None = None) -> int:
    """Neue Buchung. Zeilen (Soll, Haben, Cent > 0, Kostenstelle)."""
    _check_source(source)
    day = _check_day(booking_date)
    rows = [tuple(r) for r in rows]
    if not rows:
        raise LedgerError("Eine Buchung braucht mindestens eine Zeile.")
    _check_rows(conn, rows)
    if not periods.is_open(conn, day):
        raise LedgerError("Für dieses Datum gibt es keine offene Buchungsperiode.")
    return _insert(conn, day, rows, source=source, tx_id=tx_id,
                   opening_account_id=opening_account_id, text=text, voucher=voucher)


def _rows(conn, posting_id: int) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        "select debit_account_id, credit_account_id, cents, cost_center_id from posting_row"
        " where posting_id = ? order by id", (posting_id,))]


def reverse(conn, posting_id: int, *, source: str) -> int:
    """Storno: gespiegelte Zeilen, Datum, Umsatz und Eröffnungskonto des Originals (E4)."""
    _check_source(source)
    p = conn.execute("select id, booking_date, tx_id, opening_account_id, reversal_of"
                     " from posting where id = ?", (posting_id,)).fetchone()
    if p is None:
        raise LedgerError("Die Buchung gibt es nicht.")
    if p["reversal_of"] is not None:
        raise LedgerError("Ein Storno kann nicht storniert werden.")
    if conn.execute("select 1 from posting where reversal_of = ?", (posting_id,)).fetchone():
        raise LedgerError("Die Buchung ist schon storniert.")
    if not periods.is_open(conn, p["booking_date"]):
        raise LedgerError("Die Periode der Buchung ist abgeschlossen; ein Storno ist nicht "
                          "möglich.")
    if p["opening_account_id"] is not None:
        source = "opening"  # CHECK: Eröffnungsbestand und Herkunft gehören zusammen
    elif source == "opening":
        raise LedgerError("Unbekannte Herkunft der Buchung.")
    mirrored = [(credit, debit, cents, cc) for debit, credit, cents, cc in _rows(conn, posting_id)]
    return _insert(conn, p["booking_date"], mirrored, source=source, tx_id=p["tx_id"],
                   opening_account_id=p["opening_account_id"], reversal_of=posting_id)


# Feste SQL-Bausteine (nie aus Eingaben gebaut). Alias p für posting, t für "transaction".
VALID = ("(+p.reversal_of is null and not exists"
         " (select 1 from posting r_ where r_.reversal_of = p.id))")


def valid_posting_id(conn, tx_id: int) -> int | None:
    row = conn.execute(f"select p.id from posting p where p.tx_id = ? and {VALID}",
                       (tx_id,)).fetchone()
    return row[0] if row else None


def _tx(conn, tx_id: int) -> sqlite3.Row:
    row = conn.execute('select id, account_id, booking_date, amount_cents from "transaction"'
                       " where id = ?", (tx_id,)).fetchone()
    if row is None:
        raise LedgerError("Den Umsatz gibt es nicht.")
    return row


def book_tx(conn, tx_id: int, parts: list[Part], *, source: str) -> int:
    """Bucht einen Bankumsatz. Gleiche gültige Buchung: nichts schreiben. Sonst Storno der
    gültigen Buchung und neu buchen."""
    _check_source(source)
    if source == "opening":
        raise LedgerError("Unbekannte Herkunft der Buchung.")
    tx = _tx(conn, tx_id)
    if tx["amount_cents"] == 0:
        raise LedgerError("Umsätze über 0,00 € werden nicht gebucht.")
    if not parts:
        raise LedgerError("Es fehlt ein Gegenkonto.")
    if any(not _is_int(p.cents) or p.cents == 0 for p in parts):
        raise LedgerError("Jeder Teilbetrag muss eine ganze Zahl in Cent ungleich 0 sein.")
    if sum(p.cents for p in parts) != tx["amount_cents"]:
        raise LedgerError("Die Summe der Teilbeträge entspricht nicht dem Umsatz.")
    if len({p.account_id for p in parts}) != len(parts):
        raise LedgerError("Ein Konto kommt in der Aufteilung doppelt vor.")
    with _atomic(conn):
        bank = bank_ledger_account(conn, tx["account_id"])
        if any(p.account_id == bank for p in parts):
            raise LedgerError("Das eigene Bankkonto kann nicht Gegenkonto sein.")
        rows = [(bank, p.account_id, p.cents, p.cost_center_id) if p.cents > 0
                else (p.account_id, bank, -p.cents, p.cost_center_id) for p in parts]
        current = valid_posting_id(conn, tx_id)
        if current is not None and Counter(_rows(conn, current)) == Counter(rows):
            return current  # schon genau so gebucht (auch auf inzwischen ausgeblendetem Konto)
        _check_rows(conn, rows)
        periods.ensure_period(conn, tx["booking_date"])
        if current is not None:
            reverse(conn, current, source=source)
        return post(conn, tx["booking_date"], rows, source=source, tx_id=tx_id)


def set_counter_account(conn, tx_id: int, account_id: int | None, *,
                        cost_center_id: int | None = None, source: str) -> int | None:
    """Ein Gegenkonto für den ganzen Umsatz; None heißt "Ohne Kategorie" (stornieren)."""
    if account_id is None:
        unbook_tx(conn, tx_id, source=source)
        return None
    amount = _tx(conn, tx_id)["amount_cents"]
    return book_tx(conn, tx_id, [Part(account_id, amount, cost_center_id)], source=source)


def unbook_tx(conn, tx_id: int, *, source: str) -> bool:
    _tx(conn, tx_id)
    current = valid_posting_id(conn, tx_id)
    if current is None:
        return False
    reverse(conn, current, source=source)
    return True


def book_fees_if_unbooked(conn, tx_id: int) -> bool:
    """Eingang ohne gültige Buchung auf "Beiträge" (source 'fee'). Abgeschlossene Periode:
    False ohne Fehler."""
    tx = _tx(conn, tx_id)
    if tx["amount_cents"] <= 0 or valid_posting_id(conn, tx_id) is not None:
        return False
    period = periods.period_for(conn, tx["booking_date"])
    if period is not None and period["status"] != "open":
        return False
    book_tx(conn, tx_id, [Part(fees_account_id(conn), tx["amount_cents"])], source="fee")
    return True


def balance(conn, account_id: int, until: str | None = None) -> int:
    """Soll minus Haben über alle Zeilen bis einschließlich until (Storno hebt sich auf)."""
    sql = ("select coalesce(sum(case when r.debit_account_id = ? then r.cents else -r.cents"
           " end), 0) from posting_row r join posting p on p.id = r.posting_id"
           " where (r.debit_account_id = ? or r.credit_account_id = ?)")
    args: list = [account_id, account_id, account_id]
    if until is not None:
        sql += " and p.booking_date <= ?"
        args.append(_check_day(until))
    return conn.execute(sql, args).fetchone()[0]


# --- Eröffnungsbestand (E11, E14) --------------------------------------------------

def _valid_opening(conn, account_id: int) -> sqlite3.Row | None:
    return conn.execute(f"select p.id, p.booking_date from posting p"
                        f" where p.opening_account_id = ? and {VALID}", (account_id,)).fetchone()


def _opening_account(conn, account_id: int) -> sqlite3.Row:
    row = _account_row(conn, account_id)
    if row["kind"] not in ("asset", "liability") or row["role"] == "equity":
        raise LedgerError("Einen Eröffnungsbestand haben nur Aktiv- und Passivkonten "
                          "(nicht das Vereinsvermögen selbst).")
    return row


def get_opening(conn, account_id: int) -> tuple[int, str] | None:
    """(Cent, Tag) des gültigen Eröffnungsbestands; Aktivkonto: Guthaben positiv,
    Passivkonto: Schuld positiv."""
    row = _opening_account(conn, account_id)
    p = _valid_opening(conn, account_id)
    if p is None:
        return None
    soll = sum(cents if debit == account_id else -cents
               for debit, _credit, cents, _cc in _rows(conn, p["id"]))
    return (soll if row["kind"] == "asset" else -soll), p["booking_date"]


def set_opening(conn, account_id: int, cents: int, day: str) -> int | None:
    """Eröffnungsbestand setzen. 0 heißt keiner (stornieren). Gleicher Betrag und Tag:
    nichts schreiben. Sonst Storno und neue Buchung gegen das Vereinsvermögen."""
    row = _opening_account(conn, account_id)
    if not _is_int(cents) or abs(cents) > MAX_ROW_CENTS:
        raise LedgerError("Der Eröffnungsbestand muss eine ganze Zahl in Cent sein.")
    current = _valid_opening(conn, account_id)
    if cents == 0:
        if current is not None:
            reverse(conn, current["id"], source="opening")
        return None
    day = _check_day(day)
    equity = equity_account_id(conn)
    soll = cents if row["kind"] == "asset" else -cents
    rows = [(account_id, equity, soll, None)] if soll > 0 else [(equity, account_id, -soll, None)]
    if current is not None and current["booking_date"] == day \
            and Counter(_rows(conn, current["id"])) == Counter(rows):
        return current["id"]
    with _atomic(conn):
        if current is not None:
            reverse(conn, current["id"], source="opening")
        return post(conn, day, rows, source="opening", opening_account_id=account_id)


# --- Abfragen ----------------------------------------------------------------------

# Feste SQL-Bausteine für Abfragen mit Alias t für "transaction" (nie aus Eingaben gebaut).
_VALID_P = ("(+p_.reversal_of is null and not exists"
            " (select 1 from posting r_ where r_.reversal_of = p_.id))")
BOOKED = f"exists (select 1 from posting p_ where p_.tx_id = t.id and {_VALID_P})"
_OTHER_SIDE = ("case when w_.debit_account_id = b_.id then w_.credit_account_id"
               " else w_.debit_account_id end")
COUNTER = (f"(select case when count(distinct {_OTHER_SIDE}) = 1 then max({_OTHER_SIDE}) end"
           " from posting p_ join posting_row w_ on w_.posting_id = p_.id"
           " join ledger_account b_ on b_.bank_account_id = t.account_id"
           f" where p_.tx_id = t.id and {_VALID_P})")
ON_FEES = ("exists (select 1 from posting p_ join posting_row w_ on w_.posting_id = p_.id"
           " join ledger_account f_ on f_.role = 'fees'"
           " and f_.id in (w_.debit_account_id, w_.credit_account_id)"
           f" where p_.tx_id = t.id and {_VALID_P})")


def counter_accounts(conn, tx_ids) -> dict[int, list[int]]:
    """Gegenkonten der gültigen Buchung je Umsatz (aufsteigend), in Blöcken zu 500 IDs."""
    ids = sorted(set(tx_ids))
    out: dict[int, set] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
                "select p.tx_id, w.debit_account_id, w.credit_account_id, b.id as bank"
                " from posting p join posting_row w on w.posting_id = p.id"
                ' join "transaction" t on t.id = p.tx_id'
                " left join ledger_account b on b.bank_account_id = t.account_id"
                f" where p.tx_id in ({marks}) and {VALID}", chunk):
            other = r["credit_account_id"] if r["debit_account_id"] == r["bank"] \
                else r["debit_account_id"]
            out.setdefault(r["tx_id"], set()).add(other)
    return {k: sorted(v) for k, v in out.items()}


def money_accounts(conn, until: str) -> list[dict]:
    """Je sichtbarem Aktivkonto Eröffnungsbestand und gebuchter Stand bis until; bei
    Bankkonten zusätzlich Stand laut Umsätzen, offene Umsätze und Unterschied."""
    until = _check_day(until)
    out = []
    for a in list_accounts(conn, visible_only=True, kinds=("asset",)):
        opening = get_opening(conn, a["id"])
        cents, day = opening if opening else (0, None)
        item = {"id": a["id"], "number": a["number"], "name": a["name"],
                "opening_cents": cents, "opening_day": day,
                "booked_cents": balance(conn, a["id"], until)}
        if a["bank_account_id"] is not None:
            r = conn.execute(
                "select coalesce(sum(t.amount_cents), 0),"
                f" coalesce(sum(t.amount_cents <> 0 and not {BOOKED}), 0)"
                ' from "transaction" t where t.account_id = ? and t.booking_date <= ?',
                (a["bank_account_id"], until)).fetchone()
            statement = r[0] + (cents if day is not None and day <= until else 0)
            item.update(statement_cents=statement, unbooked=r[1],
                        diff_cents=statement - item["booked_cents"])
        out.append(item)
    return out


# --- Prüfung -----------------------------------------------------------------------

def check(conn) -> list[str]:
    """Befunde zur Stimmigkeit des Journals; leer heißt in Ordnung. Texte nennen nur
    Buchungs-, Umsatz- und Kontonummern (ohne Nummer die Konto-ID)."""
    found: list[str] = []
    label = {r[0]: ref(r[1], r[0]) for r in conn.execute("select id, number from ledger_account")}
    postings = conn.execute("select id, booking_date, tx_id, opening_account_id, reversal_of"
                            " from posting order by id").fetchall()
    rows: dict[int, list[tuple]] = {}
    for r in conn.execute("select posting_id, debit_account_id, credit_account_id, cents,"
                          " cost_center_id from posting_row order by id"):
        rows.setdefault(r[0], []).append(tuple(r)[1:])
    by_id = {p["id"]: p for p in postings}
    reversed_ids = {p["reversal_of"] for p in postings if p["reversal_of"] is not None}
    for p in postings:
        if not rows.get(p["id"]):
            found.append(f"Buchung {p['id']}: ohne Zeilen.")
    for p in postings:
        o = by_id.get(p["reversal_of"]) if p["reversal_of"] is not None else None
        if p["reversal_of"] is None:
            continue
        mirrored = Counter((cr, d, ct, cc) for d, cr, ct, cc in rows.get(o["id"], [])) \
            if o is not None else None
        if o is None or Counter(rows.get(p["id"], [])) != mirrored \
                or (p["booking_date"], p["tx_id"], p["opening_account_id"]) \
                != (o["booking_date"], o["tx_id"], o["opening_account_id"]):
            found.append(f"Buchung {p['id']}: Storno spiegelt Buchung {p['reversal_of']}"
                         " nicht genau.")
    valid = [p for p in postings if p["reversal_of"] is None and p["id"] not in reversed_ids]
    for key, what, text in (("tx_id", "Umsatz", "mehr als eine gültige Buchung."),
                            ("opening_account_id", None, "mehr als ein Eröffnungsbestand.")):
        per = Counter(p[key] for p in valid if p[key] is not None)
        for k, n in sorted(per.items()):
            if n > 1:
                found.append(f"{what} {k}: {text}" if what else f"{label.get(k, ref(None, k))}:"
                             f" {text}")
    txs = {r[0]: (r[1], r[2]) for r in conn.execute(
        'select id, account_id, amount_cents from "transaction"'
        " where id in (select tx_id from posting where tx_id is not null)")}
    bank_of = {r[0]: r[1] for r in conn.execute(
        "select bank_account_id, id from ledger_account where bank_account_id is not null")}
    for p in valid:
        if p["tx_id"] is None or p["tx_id"] not in txs:
            continue
        account_id, amount = txs[p["tx_id"]]
        if amount == 0:
            found.append(f"Umsatz {p['tx_id']}: gültige Buchung auf einem Umsatz über 0,00 €.")
        bank = bank_of.get(account_id)
        if bank is None:
            found.append(f"Buchung {p['id']}: Bankseite fehlt (Bankkonto ohne Aktivkonto).")
            continue
        side = sum(ct if d == bank else -ct for d, cr, ct, _cc in rows.get(p["id"], [])
                   if bank in (d, cr))
        if side != amount:
            found.append(f"Buchung {p['id']}: Bankseite ({label[bank]}) {side} Cent statt"
                         f" {amount} Cent.")
    for r in conn.execute("select id from account where id not in (select bank_account_id"
                          " from ledger_account where bank_account_id is not null) order by id"):
        found.append(f"Bankkonto-ID {r[0]} hat kein Aktivkonto.")
    for r in conn.execute("select p.id from posting p where not exists (select 1 from period q"
                          " where p.booking_date between q.start_date and q.end_date)"
                          " order by p.id"):
        found.append(f"Buchung {r[0]}: liegt in keiner Periode.")
    return found
