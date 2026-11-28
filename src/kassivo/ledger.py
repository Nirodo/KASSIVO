"""Buchungskern: Kontogruppen, Konten, Kostenstellen, Buchungen, Storno, Prüfung.

Doppelte Buchführung mit Buchungssatz-Zeilen (E2): jede Zeile hat Soll-Konto, Haben-Konto
und einen Betrag > 0 in Cent. Buchungen werden nie geändert oder gelöscht; Korrektur heißt
Storno plus neue Buchung (E3). Keine Funktion ruft commit(), die Aufrufer committen.
Meldungen sind deutsch und nennen nie IBAN, Namen oder Verwendungszweck.
"""
import sqlite3

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
