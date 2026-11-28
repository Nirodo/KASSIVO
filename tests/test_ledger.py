"""Buchungskern ledger.py: Kontogruppen, Konten, Kostenstellen. Nur erfundene Daten."""
import pytest

from kassivo import ledger
from kassivo.ledger import LedgerError

IBAN1 = "DE00000000000000000000"
IBAN2 = "DE11111111111111111111"


def acc(c, number):
    return c.execute("select id from ledger_account where number=?", (number,)).fetchone()[0]


def group(c, kind, name):
    return c.execute("select id from account_group where kind=? and name=?",
                     (kind, name)).fetchone()[0]


def count(c, table):
    return c.execute(f"select count(*) from {table}").fetchone()[0]


# --- Kontogruppen (E12) ------------------------------------------------------------

def test_group_create_rename_delete(ledger_conn):
    c = ledger_conn
    gid = ledger.create_group(c, "expense", " Veranstaltungen ")
    assert c.execute("select kind, name from account_group where id=?", (gid,)).fetchone()[:] \
        == ("expense", "Veranstaltungen")
    ledger.rename_group(c, gid, "Feste")
    assert ledger.find_group(c, "expense", "feste") == gid
    ledger.delete_group(c, gid)
    assert ledger.find_group(c, "expense", "Feste") is None


def test_group_validates(ledger_conn):
    c = ledger_conn
    n = count(c, "account_group")
    for kind, name in (("expense", ""), ("expense", "x" * 101), ("expense", "ausgaben"),
                       ("x", "Andere")):
        with pytest.raises(LedgerError):
            ledger.create_group(c, kind, name)
    assert count(c, "account_group") == n
    ledger.create_group(c, "income", "Ausgaben")  # gleicher Name, andere Art: erlaubt
    gid = ledger.create_group(c, "expense", "Feste")
    with pytest.raises(LedgerError):
        ledger.rename_group(c, gid, "AUSGABEN")
    ledger.rename_group(c, gid, "feste")  # eigener Name, andere Schreibweise
    with pytest.raises(LedgerError):
        ledger.rename_group(c, 999, "Neu")


def test_find_group_casefold(ledger_conn):
    c = ledger_conn
    assert ledger.find_group(c, "income", " ERTRAG ") == group(c, "income", "Ertrag")
    assert ledger.find_group(c, "expense", "Ertrag") is None


def test_delete_used_group_rejected(ledger_conn):
    c = ledger_conn
    with pytest.raises(LedgerError, match="Konten"):
        ledger.delete_group(c, group(c, "income", "Ertrag"))


def test_list_groups(ledger_conn):
    c = ledger_conn
    ledger.create_group(c, "expense", "Veranstaltungen")
    rows = ledger.list_groups(c, "expense")
    assert [(r["name"], r["accounts"]) for r in rows] == [("Ausgaben", 3),
                                                          ("Veranstaltungen", 0)]
    assert [r["kind"] for r in ledger.list_groups(c)] == \
        ["asset", "liability", "income", "expense", "expense"]


# --- Konten ------------------------------------------------------------------------

def test_create_account_validates(ledger_conn):
    c = ledger_conn
    n = count(c, "ledger_account")
    ertrag = group(c, "income", "Ertrag")
    cases = [
        dict(name="A", kind="income", number=0),
        dict(name="A", kind="income", number=100000),
        dict(name="A", kind="income", number="12"),
        dict(name="A", kind="income", number=1.0),
        dict(name="A", kind="income", number=True),
        dict(name="A", kind="income", number=3000),
        dict(name=" ", kind="income"),
        dict(name="x" * 101, kind="income"),
        dict(name="beiträge", kind="income", group_id=ertrag),
        dict(name="ohne kategorie", kind="income"),
        dict(name="A", kind="x"),
        dict(name="A", kind="expense", group_id=ertrag),
        dict(name="A", kind="income", group_id=999),
    ]
    for kw in cases:
        with pytest.raises(LedgerError):
            ledger.create_account(c, **kw)
    assert count(c, "ledger_account") == n


def test_create_account_without_number(ledger_conn):
    c = ledger_conn
    a = ledger.create_account(c, "Kasse Jugend", "asset")
    b = ledger.create_account(c, "Kasse Senioren", "asset")
    rows = c.execute("select number, group_id from ledger_account where id in (?,?)",
                     (a, b)).fetchall()
    assert [tuple(r) for r in rows] == [(None, None), (None, None)]


def test_same_name_in_other_group_or_kind_ok(ledger_conn):
    c = ledger_conn
    ledger.create_account(c, "Spenden", "expense", group_id=group(c, "expense", "Ausgaben"))
    g1 = ledger.create_group(c, "expense", "Veranstaltungen")
    g2 = ledger.create_group(c, "expense", "Jugend")
    ledger.create_account(c, "Essen", "expense", group_id=g1)
    ledger.create_account(c, "Essen", "expense", group_id=g2)
    ledger.create_account(c, "Essen", "expense")
    with pytest.raises(LedgerError, match="Gruppe"):
        ledger.create_account(c, "ESSEN", "expense", group_id=g1)
    with pytest.raises(LedgerError):
        ledger.create_account(c, "essen", "expense")


def test_update_account_renames_and_renumbers(ledger_conn):
    c = ledger_conn
    aid = acc(c, 3100)
    ledger.update_account(c, aid, name=" Spenden allgemein ", number=3150,
                          group_id=group(c, "income", "Ertrag"))
    r = c.execute("select number, name from ledger_account where id=?", (aid,)).fetchone()
    assert tuple(r) == (3150, "Spenden allgemein")
    with pytest.raises(LedgerError):
        ledger.update_account(c, aid, name="Beiträge", number=3150,
                              group_id=group(c, "income", "Ertrag"))
    with pytest.raises(LedgerError):
        ledger.update_account(c, aid, name="Spenden", number=3000,
                              group_id=group(c, "income", "Ertrag"))
    ledger.update_account(c, aid, name="spenden ALLGEMEIN", number=3150,
                          group_id=group(c, "income", "Ertrag"))  # eigener Name
    with pytest.raises(LedgerError):
        ledger.update_account(c, 999, name="X", number=None, group_id=None)


def test_update_account_group_and_clear_number(ledger_conn):
    c = ledger_conn
    aid = acc(c, 4100)
    g = ledger.create_group(c, "expense", "Bank")
    ledger.update_account(c, aid, name="Bankgebühren", number=None, group_id=g)
    r = c.execute("select number, group_id from ledger_account where id=?", (aid,)).fetchone()
    assert tuple(r) == (None, g)
    with pytest.raises(LedgerError):
        ledger.update_account(c, aid, name="Bankgebühren", number=None,
                              group_id=group(c, "income", "Ertrag"))


def test_hide_role_or_bank_account_rejected(ledger_conn):
    c = ledger_conn
    for number in (1200, 2000, 3000):
        with pytest.raises(LedgerError):
            ledger.set_account_hidden(c, acc(c, number), True)
    c.execute("insert into account(iban) values(?)", (IBAN1,))
    c.execute("update ledger_account set bank_account_id=1 where number=1000")
    with pytest.raises(LedgerError):
        ledger.set_account_hidden(c, acc(c, 1000), True)
    ledger.set_account_hidden(c, acc(c, 3100), True)
    assert c.execute("select hidden from ledger_account where number=3100").fetchone()[0] == 1
    ledger.set_account_hidden(c, acc(c, 3100), False)
    assert c.execute("select hidden from ledger_account where number=3100").fetchone()[0] == 0


def test_delete_only_unused(ledger_conn):
    c = ledger_conn
    aid = ledger.create_account(c, "Miete", "expense")
    ledger.delete_account(c, aid)
    assert c.execute("select 1 from ledger_account where id=?", (aid,)).fetchone() is None
    with pytest.raises(LedgerError):
        ledger.delete_account(c, acc(c, 3000))  # Rolle
    c.execute("insert into posting_rule(account_id, field, contains) values(?,?,?)",
              (acc(c, 3200), "purpose", "Zins"))
    with pytest.raises(LedgerError, match="benutzt"):
        ledger.delete_account(c, acc(c, 3200))
    with pytest.raises(LedgerError):
        ledger.delete_account(c, 999)


def test_list_accounts_used_counts_rows_and_rules(ledger_conn):
    c = ledger_conn
    c.execute("insert into posting(booking_date, source, created_at)"
              " values('2026-03-01','manual','2026-03-01T00:00:00')")
    c.execute("insert into posting_row(posting_id, debit_account_id, credit_account_id, cents)"
              " values(1, ?, ?, 100)", (acc(c, 1200), acc(c, 3100)))
    c.execute("insert into posting_rule(account_id, field, contains) values(?,?,?)",
              (acc(c, 3100), "purpose", "Spende"))
    used = {r["number"]: r["used"] for r in ledger.list_accounts(c)}
    assert used[3100] == 2 and used[1200] == 1 and used[3200] == 0


def test_list_accounts_order_kind_group_number(ledger_conn):
    c = ledger_conn
    g = ledger.create_group(c, "expense", "Veranstaltungen")
    ledger.create_account(c, "Essen", "expense", group_id=g)
    ledger.create_account(c, "Ohne Gruppe", "expense", number=4050)
    ledger.create_account(c, "Kasse Jugend", "asset", group_id=group(c, "asset",
                                                                    "Umlaufvermögen"))
    ledger.set_account_hidden(c, acc(c, 4100), True)
    rows = ledger.list_accounts(c)
    labels = [(r["kind"], r["group_name"], r["number"], r["name"]) for r in rows]
    assert labels == [
        ("asset", "Umlaufvermögen", 1000, "Barkasse"),
        ("asset", "Umlaufvermögen", 1200, "Bank"),
        ("asset", "Umlaufvermögen", None, "Kasse Jugend"),
        ("liability", "Eigenkapital", 2000, "Vereinsvermögen"),
        ("income", "Ertrag", 3000, "Beiträge"),
        ("income", "Ertrag", 3100, "Spenden"),
        ("income", "Ertrag", 3200, "Zinsen"),
        ("income", "Ertrag", 3900, "Sonstige Einnahmen"),
        ("expense", "Ausgaben", 4000, "Hauptverein"),
        ("expense", "Ausgaben", 4100, "Bankgebühren"),
        ("expense", "Ausgaben", 4900, "Sonstige Ausgaben"),
        ("expense", "Veranstaltungen", None, "Essen"),
        ("expense", None, 4050, "Ohne Gruppe"),
    ]
    visible = [r["number"] for r in ledger.list_accounts(c, visible_only=True,
                                                         kinds=("expense",))]
    assert visible == [4000, 4900, None, 4050]


def test_account_label(ledger_conn):
    c = ledger_conn
    ledger.create_account(c, "Kasse Jugend", "asset")
    labels = {ledger.account_label(r) for r in ledger.list_accounts(c, kinds=("asset",))}
    assert labels == {"1000 Barkasse", "1200 Bank", "Kasse Jugend"}


def test_next_free_number_skips_taken(ledger_conn):
    c = ledger_conn
    ledger.create_account(c, "A", "income", number=3500)
    ledger.create_account(c, "B", "income", number=3501)
    assert ledger.next_free_number(c, 3500) == 3502
    assert ledger.next_free_number(c, 3502) == 3502
    ledger.create_account(c, "Z", "income", number=99999)
    with pytest.raises(LedgerError):
        ledger.next_free_number(c, 99999)


def test_role_accounts(ledger_conn):
    c = ledger_conn
    assert ledger.fees_account_id(c) == acc(c, 3000)
    assert ledger.equity_account_id(c) == acc(c, 2000)


def test_bank_ledger_account_links_default_then_creates(ledger_conn):
    c = ledger_conn
    b1 = c.execute("insert into account(iban) values(?)", (IBAN1,)).lastrowid
    b2 = c.execute("insert into account(iban) values(?)", (IBAN2,)).lastrowid
    assert ledger.bank_ledger_account(c, b1) == acc(c, 1200)
    assert ledger.bank_ledger_account(c, b1) == acc(c, 1200)
    second = ledger.bank_ledger_account(c, b2)
    r = c.execute("select number, name, kind, group_id, bank_account_id from ledger_account"
                  " where id=?", (second,)).fetchone()
    assert tuple(r) == (1201, "Bank DE11…1111", "asset", group(c, "asset", "Umlaufvermögen"),
                        b2)
    assert ledger.bank_ledger_account(c, b2) == second
    with pytest.raises(LedgerError):
        ledger.bank_ledger_account(c, 999)


# --- Kostenstellen -----------------------------------------------------------------

def test_cost_center_create_rename_hide_delete(ledger_conn):
    c = ledger_conn
    cc = ledger.create_cost_center(c, " Jugend ")
    with pytest.raises(LedgerError):
        ledger.create_cost_center(c, "JUGEND")
    with pytest.raises(LedgerError):
        ledger.create_cost_center(c, "")
    ledger.rename_cost_center(c, cc, "Jugendabteilung")
    ledger.set_cost_center_hidden(c, cc, True)
    rows = ledger.list_cost_centers(c)
    assert [(r["name"], r["hidden"], r["used"]) for r in rows] == [("Jugendabteilung", 1, 0)]
    assert ledger.list_cost_centers(c, visible_only=True) == []
    ledger.delete_cost_center(c, cc)
    assert ledger.list_cost_centers(c) == []
    with pytest.raises(LedgerError):
        ledger.rename_cost_center(c, cc, "X")


def test_delete_used_cost_center_rejected(ledger_conn):
    c = ledger_conn
    cc = ledger.create_cost_center(c, "Jugend")
    c.execute("insert into posting(booking_date, source, created_at)"
              " values('2026-03-01','manual','2026-03-01T00:00:00')")
    c.execute("insert into posting_row(posting_id, debit_account_id, credit_account_id, cents,"
              " cost_center_id) values(1, ?, ?, 100, ?)", (acc(c, 1200), acc(c, 3100), cc))
    with pytest.raises(LedgerError, match="benutzt"):
        ledger.delete_cost_center(c, cc)
