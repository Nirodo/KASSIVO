"""Buchungskern ledger.py: Eröffnungsbestand, Kontostände, Abfragen, Prüfung.
Nur erfundene Daten."""
import random

import pytest

from kassivo import ledger
from kassivo.ledger import LedgerError, Part

IBAN1 = "DE00000000000000000000"
IBAN2 = "DE11111111111111111111"
NAME = "Erika Muster"


def acc(c, number):
    return c.execute("select id from ledger_account where number=?", (number,)).fetchone()[0]


def count(c, table):
    return c.execute(f"select count(*) from {table}").fetchone()[0]


def bank_tx(c, amount=1700, day="2026-03-01", iban=IBAN1):
    c.execute("insert or ignore into account(iban) values(?)", (iban,))
    aid = c.execute("select id from account where iban=?", (iban,)).fetchone()[0]
    n = c.execute('select count(*) from "transaction"').fetchone()[0]
    return c.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key, cp_name,'
        " cp_iban, purpose) values(?,?,?,?,?,?,?)",
        (aid, day, amount, f"k{n}", NAME, IBAN2, "Beitrag März")).lastrowid


def rows_of(c, pid):
    return [tuple(r) for r in c.execute(
        "select debit_account_id, credit_account_id, cents from posting_row"
        " where posting_id=? order by id", (pid,))]


# --- Eröffnungsbestand -------------------------------------------------------------

def test_opening_positive_and_negative(ledger_conn):
    c = ledger_conn
    bank, kasse, eq = acc(c, 1200), acc(c, 1000), acc(c, 2000)
    p1 = ledger.set_opening(c, bank, 50000, "2026-01-01")
    assert rows_of(c, p1) == [(bank, eq, 50000)]
    p2 = ledger.set_opening(c, kasse, -1234, "2026-01-01")
    assert rows_of(c, p2) == [(eq, kasse, 1234)]
    assert ledger.get_opening(c, bank) == (50000, "2026-01-01")
    assert ledger.get_opening(c, kasse) == (-1234, "2026-01-01")
    assert ledger.balance(c, bank) == 50000 and ledger.balance(c, kasse) == -1234
    src = c.execute("select source, opening_account_id from posting where id=?", (p1,)).fetchone()
    assert tuple(src) == ("opening", bank)


def test_opening_zero_reverses(ledger_conn):
    c = ledger_conn
    bank = acc(c, 1200)
    assert ledger.set_opening(c, bank, 0, "2026-01-01") is None
    assert count(c, "posting") == 0
    ledger.set_opening(c, bank, 50000, "2026-01-01")
    assert ledger.set_opening(c, bank, 0, "2026-01-01") is None
    assert ledger.get_opening(c, bank) is None and ledger.balance(c, bank) == 0
    assert count(c, "posting") == 2


def test_opening_same_twice_is_noop(ledger_conn):
    c = ledger_conn
    p1 = ledger.set_opening(c, acc(c, 1200), 50000, "2026-01-01")
    assert ledger.set_opening(c, acc(c, 1200), 50000, "2026-01-01") == p1
    assert count(c, "posting") == 1


def test_opening_change_reverses_and_rebooks(ledger_conn):
    c = ledger_conn
    bank = acc(c, 1200)
    p1 = ledger.set_opening(c, bank, 50000, "2026-01-01")
    p2 = ledger.set_opening(c, bank, 40000, "2026-01-01")
    p3 = ledger.set_opening(c, bank, 40000, "2026-01-02")
    assert len({p1, p2, p3}) == 3 and count(c, "posting") == 5
    assert ledger.get_opening(c, bank) == (40000, "2026-01-02")
    assert ledger.balance(c, bank) == 40000 and ledger.balance(c, acc(c, 2000)) == -40000


def test_opening_only_for_asset_and_liability(ledger_conn):
    c = ledger_conn
    for number in (3000, 4000, 2000):
        with pytest.raises(LedgerError):
            ledger.set_opening(c, acc(c, number), 100, "2026-01-01")
    loan = ledger.create_account(c, "Darlehen", "liability")
    pid = ledger.set_opening(c, loan, 30000, "2026-01-01")
    assert rows_of(c, pid) == [(acc(c, 2000), loan, 30000)]
    assert ledger.get_opening(c, loan) == (30000, "2026-01-01")
    assert ledger.balance(c, loan) == -30000  # Soll minus Haben: Passivkonto im Haben


def test_opening_validates(ledger_conn):
    c = ledger_conn
    bank = acc(c, 1200)
    for cents, day in ((1.5, "2026-01-01"), (True, "2026-01-01"), (100, "2025-01-01"),
                       (100, "kein Datum")):
        with pytest.raises(LedgerError):
            ledger.set_opening(c, bank, cents, day)
    with pytest.raises(LedgerError):
        ledger.set_opening(c, 999, 100, "2026-01-01")
    assert count(c, "posting") == 0


def test_opening_does_not_create_period(ledger_conn):
    c = ledger_conn
    with pytest.raises(LedgerError, match="keine offene Buchungsperiode"):
        ledger.set_opening(c, acc(c, 1200), 100, "2030-01-01")
    assert count(c, "period") == 1


def test_balance_until_date(ledger_conn):
    c = ledger_conn
    for amount, day in ((1000, "2026-01-10"), (-300, "2026-02-10"), (500, "2026-03-10")):
        ledger.set_counter_account(c, bank_tx(c, amount, day),
                                   acc(c, 3900) if amount > 0 else acc(c, 4900), source="manual")
    bank = acc(c, 1200)
    assert ledger.balance(c, bank, "2026-01-09") == 0
    assert ledger.balance(c, bank, "2026-01-10") == 1000
    assert ledger.balance(c, bank, "2026-02-28") == 700
    assert ledger.balance(c, bank) == 1200


def test_money_accounts_shows_unbooked_difference(ledger_conn):
    c = ledger_conn
    bank = acc(c, 1200)
    ledger.set_opening(c, bank, 10000, "2026-01-01")
    ledger.set_counter_account(c, bank_tx(c, 1700, "2026-03-01"), acc(c, 3000),
                               source="manual")
    bank_tx(c, -300, "2026-03-02")
    bank_tx(c, 0, "2026-03-03")  # 0 Cent wird nie gebucht und zählt nicht als offen
    bank_tx(c, 999, "2026-12-01")  # nach dem Stichtag
    rows = {r["number"]: r for r in ledger.money_accounts(c, "2026-11-30")}
    b = rows[1200]
    assert (b["opening_cents"], b["opening_day"], b["booked_cents"], b["statement_cents"],
            b["unbooked"], b["diff_cents"]) == (10000, "2026-01-01", 11700, 11400, 1, -300)
    k = rows[1000]
    assert k["booked_cents"] == 0 and k["opening_cents"] == 0 and "statement_cents" not in k


def test_money_accounts_skips_hidden(ledger_conn):
    c = ledger_conn
    extra = ledger.create_account(c, "Kasse Fest", "asset")
    assert extra in [r["id"] for r in ledger.money_accounts(c, "2026-12-31")]
    ledger.set_account_hidden(c, extra, True)
    ids = [r["id"] for r in ledger.money_accounts(c, "2026-12-31")]
    assert extra not in ids and acc(c, 1200) in ids


# --- Abfragen ----------------------------------------------------------------------

def test_counter_accounts_for_split_and_reversed(ledger_conn):
    c = ledger_conn
    t1, t2, t3, t4 = bank_tx(c, 1000), bank_tx(c, 500), bank_tx(c, 200), bank_tx(c, -50)
    ledger.book_tx(c, t1, [Part(acc(c, 3100), 600), Part(acc(c, 3000), 400)], source="manual")
    ledger.set_counter_account(c, t2, acc(c, 3000), source="manual")
    ledger.set_counter_account(c, t2, acc(c, 3200), source="manual")
    ledger.set_counter_account(c, t3, acc(c, 3000), source="manual")
    ledger.set_counter_account(c, t3, None, source="manual")
    got = ledger.counter_accounts(c, [t1, t2, t3, t4, 999])
    assert got == {t1: sorted([acc(c, 3000), acc(c, 3100)]), t2: [acc(c, 3200)]}


def test_counter_accounts_many_ids(ledger_conn):
    c = ledger_conn
    ids = [bank_tx(c, 100) for _ in range(1100)]
    for tx in ids[::2]:
        ledger.set_counter_account(c, tx, acc(c, 3000), source="manual")
    got = ledger.counter_accounts(c, ids)
    assert len(got) == 550 and all(v == [acc(c, 3000)] for v in got.values())


def test_sql_fragments_match_python(ledger_conn):
    c = ledger_conn
    rnd = random.Random(57)
    fees, others = acc(c, 3000), [acc(c, n) for n in (3100, 3200, 4000, 4100)]
    ids = []
    for i in range(30):
        iban = IBAN1 if i % 3 else IBAN2
        amount = rnd.choice([-1, 1]) * rnd.randint(1, 5000)
        tx = bank_tx(c, amount, f"2026-0{rnd.randint(1, 9)}-1{rnd.randint(0, 9)}", iban)
        ids.append(tx)
        for _ in range(rnd.randint(0, 3)):
            choice = rnd.random()
            if choice < 0.2:
                ledger.set_counter_account(c, tx, None, source="manual")
            elif choice < 0.5 and abs(amount) > 1:
                first = rnd.randint(1, abs(amount) - 1) * (1 if amount > 0 else -1)
                a, b = rnd.sample([fees] + others, 2)
                ledger.book_tx(c, tx, [Part(a, first), Part(b, amount - first)], source="manual")
            else:
                ledger.set_counter_account(c, tx, rnd.choice([fees] + others), source="manual")
    sql = (f"select t.id, {ledger.BOOKED} as booked, {ledger.COUNTER} as counter,"
           f" {ledger.ON_FEES} as on_fees from \"transaction\" t order by t.id")
    py = ledger.counter_accounts(c, ids)
    for r in c.execute(sql):
        valid = ledger.valid_posting_id(c, r["id"])
        assert bool(r["booked"]) == (valid is not None)
        counters = py.get(r["id"], [])
        assert r["counter"] == (counters[0] if len(counters) == 1 else None)
        assert bool(r["on_fees"]) == (fees in counters)


# --- Prüfung -----------------------------------------------------------------------

def _raw_posting(c, day="2026-03-01", tx=None, rev=None):
    return c.execute("insert into posting(booking_date, tx_id, reversal_of, source, created_at)"
                     " values(?,?,?,'manual','2026-03-01T00:00:00')", (day, tx, rev)).lastrowid


def _raw_row(c, pid, debit, credit, cents):
    c.execute("insert into posting_row(posting_id, debit_account_id, credit_account_id, cents)"
              " values(?,?,?,?)", (pid, debit, credit, cents))


def test_check_clean_db_empty(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    ledger.set_counter_account(c, tx, acc(c, 3000), source="manual")
    ledger.set_counter_account(c, tx, acc(c, 3100), source="manual")
    ledger.set_opening(c, acc(c, 1200), 500, "2026-01-01")
    ledger.book_tx(c, bank_tx(c, 900, iban=IBAN2), [Part(acc(c, 3000), 1000),
                                                    Part(acc(c, 4100), -100)], source="manual")
    assert ledger.check(c) == []


def test_check_finds_bank_side_mismatch(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    ledger.bank_ledger_account(c, 1)
    pid = _raw_posting(c, tx=tx)
    _raw_row(c, pid, acc(c, 1200), acc(c, 3000), 1600)
    found = ledger.check(c)
    assert len(found) == 1 and f"Buchung {pid}" in found[0] and "Bankseite" in found[0]


def test_check_finds_posting_without_rows(ledger_conn):
    c = ledger_conn
    pid = _raw_posting(c)
    assert ledger.check(c) == [f"Buchung {pid}: ohne Zeilen."]


def test_check_finds_bad_reversal(ledger_conn):
    c = ledger_conn
    p1 = _raw_posting(c)
    _raw_row(c, p1, acc(c, 1000), acc(c, 3900), 500)
    r1 = _raw_posting(c, rev=p1)
    _raw_row(c, r1, acc(c, 3900), acc(c, 1000), 400)
    found = ledger.check(c)
    assert any(f"Buchung {r1}" in f and "Storno" in f for f in found)


def test_check_finds_zero_tx_booked_and_missing_bank_link(ledger_conn):
    c = ledger_conn
    ledger.bank_ledger_account(c, c.execute("insert into account(iban) values(?)",
                                            (IBAN1,)).lastrowid)
    tx = bank_tx(c, 0)
    pid = _raw_posting(c, tx=tx)
    _raw_row(c, pid, acc(c, 1200), acc(c, 3000), 1)
    c.execute("insert into account(iban) values(?)", (IBAN2,))
    found = ledger.check(c)
    assert any("0,00" in f for f in found)
    assert any("Bankkonto-ID 2" in f for f in found)


def test_check_finds_posting_outside_period(ledger_conn):
    c = ledger_conn
    pid = _raw_posting(c, "2026-03-01")
    _raw_row(c, pid, acc(c, 1000), acc(c, 3900), 5)
    c.execute("drop trigger period_keep")
    c.execute("delete from period")
    assert any(f"Buchung {pid}" in f and "Periode" in f for f in ledger.check(c))


def test_check_messages_without_personal_data(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    ledger.bank_ledger_account(c, 1)
    pid = _raw_posting(c, tx=tx)
    _raw_row(c, pid, acc(c, 1200), acc(c, 3000), 1)
    _raw_posting(c)
    text = " ".join(ledger.check(c))
    assert text
    for secret in (NAME, "Erika", IBAN1, IBAN2, "DE00", "DE11", "Beitrag März", "Beiträge"):
        assert secret not in text


def test_check_unnumbered_account_uses_id(ledger_conn):
    c = ledger_conn
    extra = ledger.create_account(c, "Kasse Fest", "asset")
    c.execute("insert into account(iban) values(?)", (IBAN1,))
    c.execute("update ledger_account set bank_account_id=1 where id=?", (extra,))
    tx = bank_tx(c, 1700)
    pid = _raw_posting(c, tx=tx)
    _raw_row(c, pid, extra, acc(c, 3000), 1)
    found = ledger.check(c)
    assert any(f"Konto-ID {extra}" in f for f in found)
