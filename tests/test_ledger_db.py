"""Buchungstabellen und Trigger von Schema 4 (rohes SQL, ohne ledger.py). Nur erfundene Daten."""
import sqlite3
from datetime import date

import pytest

from kassivo import db, restore

TODAY = date(2026, 11, 28)
IBAN1 = "DE00000000000000000000"
IBAN2 = "DE11111111111111111111"


def _install(c, today=TODAY):
    for stmt in db._statements(db._LEDGER_DDL_4):
        c.execute(stmt)
    db._seed_ledger_4(c, today)
    c.commit()


@pytest.fixture
def lc():
    c = db.connect(":memory:")
    _install(c)
    yield c
    c.close()


def acc(c, number):
    return c.execute("select id from ledger_account where number=?", (number,)).fetchone()[0]


def bank_tx(c, amount=1700, day="2026-03-01", iban=IBAN1, key=None):
    c.execute("insert or ignore into account(iban) values(?)", (iban,))
    aid = c.execute("select id from account where iban=?", (iban,)).fetchone()[0]
    n = c.execute('select count(*) from "transaction"').fetchone()[0]
    cur = c.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key, cp_name,'
        " cp_iban, purpose) values(?,?,?,?,?,?,?)",
        (aid, day, amount, key or f"k{n}", "Erika Muster", IBAN2, "Beitrag März"))
    return cur.lastrowid


def posting(c, day="2026-03-01", *, tx=None, rev=None, opening=None, source="manual"):
    cur = c.execute(
        "insert into posting(booking_date, tx_id, reversal_of, opening_account_id, source,"
        " created_at) values(?,?,?,?,?,?)",
        (day, tx, rev, opening, source, "2026-11-28T10:00:00"))
    return cur.lastrowid


def row(c, pid, debit, credit, cents, cc=None):
    c.execute("insert into posting_row(posting_id, debit_account_id, credit_account_id, cents,"
              " cost_center_id) values(?,?,?,?,?)", (pid, debit, credit, cents, cc))


def book(c, tx, debit, credit, cents, day="2026-03-01", rev=None):
    pid = posting(c, day, tx=tx, rev=rev)
    row(c, pid, debit, credit, cents)
    return pid


# --- Beträge und Zeilen -------------------------------------------------------------

@pytest.mark.parametrize("cents", [0, -1, 1.5, "1.5", "100x", None])
def test_row_amount_zero_negative_float_rejected(lc, cents):
    pid = posting(lc)
    with pytest.raises(sqlite3.IntegrityError):
        row(lc, pid, acc(lc, 1200), acc(lc, 3000), cents)


def test_row_amount_text_digits_become_integer(lc):
    """Integer-Affinität: '100' wird exakt als Zahl 100 gespeichert, nie als Text oder Float."""
    pid = posting(lc)
    row(lc, pid, acc(lc, 1200), acc(lc, 3000), "100")
    got = lc.execute("select typeof(cents), cents from posting_row").fetchone()
    assert tuple(got) == ("integer", 100)


def test_same_account_both_sides_rejected(lc):
    pid = posting(lc)
    with pytest.raises(sqlite3.IntegrityError):
        row(lc, pid, acc(lc, 1200), acc(lc, 1200), 100)


def test_update_and_delete_of_posting_and_row_rejected(lc):
    pid = posting(lc)
    row(lc, pid, acc(lc, 1200), acc(lc, 3000), 100)
    for sql in ("update posting set text='x'", "delete from posting",
                "update posting_row set cents=5", "delete from posting_row"):
        with pytest.raises(sqlite3.IntegrityError, match="Storno"):
            lc.execute(sql)


def test_row_for_older_posting_rejected(lc):
    p1 = posting(lc)
    row(lc, p1, acc(lc, 1200), acc(lc, 3000), 100)
    posting(lc)
    with pytest.raises(sqlite3.IntegrityError, match="abgeschlossen"):
        row(lc, p1, acc(lc, 1200), acc(lc, 3100), 50)


# --- gültige Buchung je Umsatz und Storno --------------------------------------------

def test_second_valid_posting_for_tx_rejected(lc):
    tx = bank_tx(lc)
    book(lc, tx, acc(lc, 1200), acc(lc, 3000), 1700)
    with pytest.raises(sqlite3.IntegrityError, match="gültige Buchung"):
        posting(lc, tx=tx)


def test_after_reversal_new_posting_allowed(lc):
    tx = bank_tx(lc)
    p1 = book(lc, tx, acc(lc, 1200), acc(lc, 3000), 1700)
    book(lc, tx, acc(lc, 3000), acc(lc, 1200), 1700, rev=p1)
    book(lc, tx, acc(lc, 1200), acc(lc, 3100), 1700)
    assert lc.execute("select count(*) from posting").fetchone()[0] == 3


def test_reversal_twice_rejected(lc):
    tx = bank_tx(lc)
    p1 = book(lc, tx, acc(lc, 1200), acc(lc, 3000), 1700)
    posting(lc, tx=tx, rev=p1)
    with pytest.raises(sqlite3.IntegrityError):
        posting(lc, tx=tx, rev=p1)


def test_reversal_of_reversal_rejected(lc):
    tx = bank_tx(lc)
    p1 = book(lc, tx, acc(lc, 1200), acc(lc, 3000), 1700)
    r1 = posting(lc, tx=tx, rev=p1)
    with pytest.raises(sqlite3.IntegrityError, match="Storno kann nicht storniert"):
        posting(lc, tx=tx, rev=r1)


def test_reversal_other_tx_rejected(lc):
    tx1, tx2 = bank_tx(lc), bank_tx(lc)
    p1 = book(lc, tx1, acc(lc, 1200), acc(lc, 3000), 1700)
    with pytest.raises(sqlite3.IntegrityError, match="anderen Umsatz"):
        posting(lc, tx=tx2, rev=p1)


def test_reversal_of_missing_rejected(lc):
    with pytest.raises(sqlite3.IntegrityError, match="ohne stornierte"):
        posting(lc, rev=999)


# --- Eröffnungsbestand -------------------------------------------------------------

def test_second_opening_for_account_rejected(lc):
    bank = acc(lc, 1200)
    p1 = posting(lc, opening=bank, source="opening")
    row(lc, p1, bank, acc(lc, 2000), 5000)
    with pytest.raises(sqlite3.IntegrityError, match="Eröffnungsbestand"):
        posting(lc, opening=bank, source="opening")
    posting(lc, opening=bank, source="opening", rev=p1)
    posting(lc, opening=bank, source="opening")  # nach Storno wieder erlaubt


def test_opening_requires_source_opening(lc):
    with pytest.raises(sqlite3.IntegrityError):
        posting(lc, opening=acc(lc, 1200), source="manual")
    with pytest.raises(sqlite3.IntegrityError):
        posting(lc, source="opening")


def test_opening_and_tx_together_rejected(lc):
    tx = bank_tx(lc)
    with pytest.raises(sqlite3.IntegrityError):
        posting(lc, tx=tx, opening=acc(lc, 1200), source="opening")


# --- Perioden ----------------------------------------------------------------------

def _period(c, name, start, end, status="open"):
    closed = "2026-11-28T10:00:00" if status == "closed" else None
    return c.execute("insert into period(name, start_date, end_date, status, closed_at)"
                     " values(?,?,?,?,?)", (name, start, end, status, closed)).lastrowid


def test_posting_outside_period_rejected(lc):
    with pytest.raises(sqlite3.IntegrityError, match="keine offene Buchungsperiode"):
        posting(lc, "2025-12-31")


def test_posting_in_closed_period_rejected(lc):
    tx = bank_tx(lc, day="2025-05-01")
    _period(lc, "Jahr 2025", "2025-01-01", "2025-12-31")
    p1 = book(lc, tx, acc(lc, 1200), acc(lc, 3000), 1700, day="2025-05-01")
    lc.execute("update period set status='closed', closed_at='2026-01-02T00:00:00'"
               " where name='Jahr 2025'")
    with pytest.raises(sqlite3.IntegrityError, match="keine offene"):
        posting(lc, "2025-06-01")
    with pytest.raises(sqlite3.IntegrityError, match="keine offene"):
        posting(lc, "2025-05-01", tx=tx, rev=p1)


def test_period_overlap_rejected(lc):
    with pytest.raises(sqlite3.IntegrityError, match="überschneiden"):
        _period(lc, "Vereinsjahr", "2026-07-01", "2027-06-30")
    pid = _period(lc, "Jahr 2027", "2027-01-01", "2027-12-31")
    with pytest.raises(sqlite3.IntegrityError, match="überschneiden"):
        lc.execute("update period set start_date='2026-12-01' where id=?", (pid,))


def test_period_shrink_with_postings_rejected(lc):
    posting(lc, "2026-03-01")
    with pytest.raises(sqlite3.IntegrityError, match="außerhalb"):
        lc.execute("update period set start_date='2026-04-01' where name='Jahr 2026'")
    lc.execute("update period set end_date='2026-06-30' where name='Jahr 2026'")


def test_closed_period_bounds_fixed(lc):
    pid = _period(lc, "Jahr 2025", "2025-01-01", "2025-12-31", status="closed")
    with pytest.raises(sqlite3.IntegrityError, match="abgeschlossene"):
        lc.execute("update period set end_date='2025-06-30' where id=?", (pid,))


def test_period_with_postings_not_deletable(lc):
    posting(lc, "2026-03-01")
    with pytest.raises(sqlite3.IntegrityError, match="enthält Buchungen"):
        lc.execute("delete from period where name='Jahr 2026'")
    pid = _period(lc, "Jahr 2027", "2027-01-01", "2027-12-31")
    lc.execute("delete from period where id=?", (pid,))


def test_closed_requires_closed_at(lc):
    with pytest.raises(sqlite3.IntegrityError):
        lc.execute("insert into period(name, start_date, end_date, status)"
                   " values('x','2030-01-01','2030-12-31','closed')")
    with pytest.raises(sqlite3.IntegrityError):
        lc.execute("insert into period(name, start_date, end_date, closed_at)"
                   " values('y','2031-01-01','2031-12-31','2031-12-31T00:00:00')")
    with pytest.raises(sqlite3.IntegrityError):
        _period(lc, "z", "2032-12-31", "2032-01-01")


# --- Kostenstellen, Konten, Regeln -------------------------------------------------

def test_cost_center_on_asset_only_rejected(lc):
    cc = lc.execute("insert into cost_center(name) values('Jugend')").lastrowid
    pid = posting(lc)
    with pytest.raises(sqlite3.IntegrityError, match="Kostenstellen"):
        row(lc, pid, acc(lc, 1200), acc(lc, 1000), 100, cc)


def test_cost_center_on_income_ok(lc):
    cc = lc.execute("insert into cost_center(name) values('Jugend')").lastrowid
    pid = posting(lc)
    row(lc, pid, acc(lc, 1200), acc(lc, 3000), 100, cc)


def test_kind_change_of_used_account_rejected(lc):
    pid = posting(lc)
    row(lc, pid, acc(lc, 1200), acc(lc, 3100), 100)
    with pytest.raises(sqlite3.IntegrityError, match="benutzten Kontos"):
        lc.execute("update ledger_account set kind='expense', group_id=null where number=3100")
    lc.execute("insert into posting_rule(account_id, field, contains) values(?,?,?)",
               (acc(lc, 3200), "purpose", "Zins"))
    with pytest.raises(sqlite3.IntegrityError, match="benutzten Kontos"):
        lc.execute("update ledger_account set kind='expense', group_id=null where number=3200")


def test_kind_change_of_unused_account_ok(lc):
    lc.execute("update ledger_account set kind='expense', group_id=null where number=3900")
    assert lc.execute("select kind from ledger_account where number=3900").fetchone()[0] == \
        "expense"


def test_bank_link_change_rejected(lc):
    lc.execute("insert into account(iban) values(?)", (IBAN1,))
    lc.execute("update ledger_account set bank_account_id=1 where number=1200")
    with pytest.raises(sqlite3.IntegrityError, match="Bankkonto"):
        lc.execute("update ledger_account set bank_account_id=null where number=1200")


def test_role_and_bank_accounts_not_deletable(lc):
    with pytest.raises(sqlite3.IntegrityError, match="braucht KASSIVO"):
        lc.execute("delete from ledger_account where number=3000")
    lc.execute("insert into account(iban) values(?)", (IBAN1,))
    lc.execute("update ledger_account set bank_account_id=1 where number=1000")
    with pytest.raises(sqlite3.IntegrityError, match="braucht KASSIVO"):
        lc.execute("delete from ledger_account where number=1000")


def test_used_account_not_deletable_by_fk(lc):
    pid = posting(lc)
    row(lc, pid, acc(lc, 1200), acc(lc, 3100), 100)
    with pytest.raises(sqlite3.IntegrityError):
        lc.execute("delete from ledger_account where number=3100")


def test_rule_on_asset_rejected(lc):
    with pytest.raises(sqlite3.IntegrityError, match="Regeln"):
        lc.execute("insert into posting_rule(account_id, field, contains) values(?,?,?)",
                   (acc(lc, 1000), "purpose", "x"))
    rid = lc.execute("insert into posting_rule(account_id, field, contains) values(?,?,?)",
                     (acc(lc, 3100), "purpose", "x")).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="Regeln"):
        lc.execute("update posting_rule set account_id=? where id=?", (acc(lc, 2000), rid))


def test_role_account_cannot_be_hidden(lc):
    with pytest.raises(sqlite3.IntegrityError):
        lc.execute("update ledger_account set hidden=1 where number=3000")


def test_bank_link_only_on_asset(lc):
    lc.execute("insert into account(iban) values(?)", (IBAN1,))
    with pytest.raises(sqlite3.IntegrityError):
        lc.execute("update ledger_account set bank_account_id=1 where number=3100")


# --- Gruppen, Nummern, Namen (E12, E13) --------------------------------------------

def _group(c, kind, name):
    return c.execute("insert into account_group(kind, name) values(?,?)", (kind, name)).lastrowid


def _account(c, name, kind, number=None, group=None):
    return c.execute("insert into ledger_account(number, name, kind, group_id) values(?,?,?,?)",
                     (number, name, kind, group)).lastrowid


def test_number_optional_and_unique(lc):
    _account(lc, "Kasse Jugend", "asset")
    _account(lc, "Kasse Senioren", "asset")
    with pytest.raises(sqlite3.IntegrityError):
        _account(lc, "Kasse Fest", "asset", number=1000)
    for bad in (0, 100000, 1.5, "12x"):
        with pytest.raises(sqlite3.IntegrityError):
            _account(lc, f"Konto {bad}", "asset", number=bad)


def test_same_name_other_kind_or_group_ok(lc):
    _account(lc, "Spenden", "expense")  # 3100 "Spenden" ist ein Ertragskonto
    g1, g2 = _group(lc, "expense", "Veranstaltungen"), _group(lc, "expense", "Jugend")
    _account(lc, "Essen", "expense", group=g1)
    _account(lc, "Essen", "expense", group=g2)
    assert lc.execute("select count(*) from ledger_account where name='Essen'").fetchone()[0] == 2


def test_same_name_same_group_rejected(lc):
    g = _group(lc, "expense", "Veranstaltungen")
    _account(lc, "Essen", "expense", group=g)
    with pytest.raises(sqlite3.IntegrityError):
        _account(lc, "ESSEN", "expense", group=g)
    _account(lc, "Miete", "expense")
    with pytest.raises(sqlite3.IntegrityError):
        _account(lc, "miete", "expense")


def test_group_kind_must_match(lc):
    g = _group(lc, "income", "Weiterer Ertrag")
    with pytest.raises(sqlite3.IntegrityError, match="Kontogruppe"):
        _account(lc, "Essen", "expense", group=g)
    with pytest.raises(sqlite3.IntegrityError, match="Kontogruppe"):
        lc.execute("update ledger_account set group_id=? where number=4100", (g,))
    with pytest.raises(sqlite3.IntegrityError, match="Kontogruppe"):
        lc.execute("update ledger_account set kind='expense' where number=3900")


def test_group_name_unique_per_kind(lc):
    _group(lc, "income", "Veranstaltungen")
    _group(lc, "expense", "Veranstaltungen")
    with pytest.raises(sqlite3.IntegrityError):
        _group(lc, "income", "veranstaltungen")
    with pytest.raises(sqlite3.IntegrityError):
        _group(lc, "x", "Andere")
    with pytest.raises(sqlite3.IntegrityError):
        _group(lc, "income", "  ")


def test_used_group_kind_fixed(lc):
    g = lc.execute("select group_id from ledger_account where number=3000").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="Kontogruppe"):
        lc.execute("update account_group set kind='expense' where id=?", (g,))
    empty = _group(lc, "income", "Leer")
    lc.execute("update account_group set kind='expense' where id=?", (empty,))


def test_used_group_not_deletable_by_fk(lc):
    g = lc.execute("select group_id from ledger_account where number=3000").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        lc.execute("delete from account_group where id=?", (g,))


def test_seed_groups(lc):
    rows = lc.execute(
        "select a.number, g.kind, g.name from ledger_account a"
        " join account_group g on g.id = a.group_id order by a.number").fetchall()
    got = {r[0]: (r[1], r[2]) for r in rows}
    assert got[1000] == ("asset", "Umlaufvermögen")
    assert got[1200] == ("asset", "Umlaufvermögen")
    assert got[2000] == ("liability", "Eigenkapital")
    assert got[3000] == ("income", "Ertrag")
    assert got[4900] == ("expense", "Ausgaben")
    assert len(got) == 10


# --- Härtung und Seed --------------------------------------------------------------

def test_triggers_work_under_hardened_connection(tmp_path):
    path = tmp_path / "k.db"
    c = db.connect(path, on_open=restore.harden)
    try:
        _install(c)
        assert c.execute("pragma trusted_schema").fetchone()[0] == 0
        tx = bank_tx(c)
        p1 = book(c, tx, acc(c, 1200), acc(c, 3000), 1700)
        book(c, tx, acc(c, 3000), acc(c, 1200), 1700, rev=p1)
        book(c, tx, acc(c, 1200), acc(c, 3100), 1700)
        with pytest.raises(sqlite3.IntegrityError, match="gültige Buchung"):
            posting(c, tx=tx)
        g = _group(c, "expense", "Veranstaltungen")
        _account(c, "Essen", "expense", group=g)
        with pytest.raises(sqlite3.IntegrityError):
            _account(c, "essen", "expense", group=g)
        c.commit()
    finally:
        c.close()


def test_seed_links_first_bank_and_creates_more():
    c = db.connect(":memory:")
    bank_tx(c, iban=IBAN1, day="2026-01-05")
    bank_tx(c, iban=IBAN2, day="2026-01-06")
    _install(c)
    rows = c.execute("select a.number, a.name, a.role, a.group_id, b.iban from ledger_account a"
                     " left join account b on b.id = a.bank_account_id"
                     " where a.kind='asset' order by a.number").fetchall()
    by_number = {r["number"]: r for r in rows}
    assert by_number[1200]["iban"] == IBAN1 and by_number[1200]["name"] == "Bank"
    assert by_number[1201]["iban"] == IBAN2 and by_number[1201]["name"] == "Bank DE11…1111"
    assert by_number[1201]["group_id"] == by_number[1200]["group_id"]
    assert by_number[1000]["iban"] is None
    plan = c.execute("select number, name, kind, role from ledger_account"
                     " where number <> 1201 order by number").fetchall()
    assert [tuple(r) for r in plan] == [tuple(t[:4]) for t in db.START_PLAN_4]
    roles = [r[0] for r in c.execute("select role from ledger_account where role is not null")]
    assert sorted(roles) == ["bank_default", "equity", "fees"]
    c.close()


def test_seed_periods_from_first_tx_year():
    c = db.connect(":memory:")
    bank_tx(c, day="2023-04-01")
    bank_tx(c, day="2025-04-01")
    _install(c)
    rows = c.execute("select name, start_date, end_date, status from period"
                     " order by start_date").fetchall()
    assert [tuple(r) for r in rows] == [
        (f"Jahr {y}", f"{y}-01-01", f"{y}-12-31", "open") for y in (2023, 2024, 2025, 2026)]
    active = c.execute("select value from settings where key='active_period'").fetchone()[0]
    assert c.execute("select name from period where id=?", (int(active),)).fetchone()[0] == \
        "Jahr 2026"
    c.close()


def test_seed_without_tx_only_current_year(lc):
    assert [r[0] for r in lc.execute("select name from period")] == ["Jahr 2026"]


def test_seed_does_not_commit():
    c = db.connect(":memory:")
    for stmt in db._statements(db._LEDGER_DDL_4):
        c.execute(stmt)
    c.commit()
    db._seed_ledger_4(c, TODAY)
    assert c.in_transaction
    c.rollback()
    assert c.execute("select count(*) from ledger_account").fetchone()[0] == 0
    c.close()


def test_ledger_ddl_not_in_migrations_yet():
    assert 4 not in db.MIGRATIONS
    assert db.SCHEMA_VERSION == 3
