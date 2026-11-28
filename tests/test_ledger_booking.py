"""Buchungskern ledger.py: Buchen, Storno, Beiträge. Nur erfundene Daten."""
import pytest

from kassivo import ledger
from kassivo.ledger import LedgerError, Part

IBAN1 = "DE00000000000000000000"
IBAN2 = "DE11111111111111111111"


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
        (aid, day, amount, f"k{n}", "Erika Muster", IBAN2, "Beitrag März")).lastrowid


def rows_of(c, pid):
    return [tuple(r) for r in c.execute(
        "select debit_account_id, credit_account_id, cents, cost_center_id from posting_row"
        " where posting_id=? order by id", (pid,))]


def valid_count(c, tx):
    return c.execute(
        "select count(*) from posting p where p.tx_id=? and p.reversal_of is null"
        " and not exists (select 1 from posting r where r.reversal_of=p.id)", (tx,)).fetchone()[0]


def close_all(c):
    c.execute("update period set status='closed', closed_at='2027-01-02T00:00:00'")


# --- Buchen ------------------------------------------------------------------------

def test_book_income_debits_bank(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    pid = ledger.book_tx(c, tx, [Part(acc(c, 3000), 1700)], source="manual")
    assert rows_of(c, pid) == [(acc(c, 1200), acc(c, 3000), 1700, None)]
    p = c.execute("select booking_date, tx_id, source, text from posting where id=?",
                  (pid,)).fetchone()
    assert tuple(p) == ("2026-03-01", tx, "manual", None)


def test_book_expense_credits_bank(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, -500)
    pid = ledger.book_tx(c, tx, [Part(acc(c, 4000), -500)], source="manual")
    assert rows_of(c, pid) == [(acc(c, 4000), acc(c, 1200), 500, None)]


def test_book_zero_amount_rejected(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 0)
    with pytest.raises(LedgerError, match="0,00"):
        ledger.book_tx(c, tx, [Part(acc(c, 3000), 0)], source="manual")
    assert count(c, "posting") == 0


def test_book_validates_parts(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    for parts in ([], [Part(acc(c, 3000), 1700), Part(acc(c, 3100), 0)],
                  [Part(acc(c, 3000), 1700.0)], [Part(acc(c, 3000), True)],
                  [Part(999, 1700)]):
        with pytest.raises(LedgerError):
            ledger.book_tx(c, tx, parts, source="manual")
    with pytest.raises(LedgerError):
        ledger.book_tx(c, 999, [Part(acc(c, 3000), 1700)], source="manual")
    for source in ("opening", "x"):
        with pytest.raises(LedgerError):
            ledger.book_tx(c, tx, [Part(acc(c, 3000), 1700)], source=source)
    assert count(c, "posting") == 0


def test_parts_sum_must_equal_amount(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    with pytest.raises(LedgerError, match="Summe"):
        ledger.book_tx(c, tx, [Part(acc(c, 3000), 1000), Part(acc(c, 3100), 699)],
                       source="manual")
    pid = ledger.book_tx(c, tx, [Part(acc(c, 3000), 1000), Part(acc(c, 3100), 700)],
                         source="manual")
    assert rows_of(c, pid) == [(acc(c, 1200), acc(c, 3000), 1000, None),
                               (acc(c, 1200), acc(c, 3100), 700, None)]


def test_split_with_mixed_signs(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1000)
    pid = ledger.book_tx(c, tx, [Part(acc(c, 3000), 1200), Part(acc(c, 4100), -200)],
                         source="manual")
    assert rows_of(c, pid) == [(acc(c, 1200), acc(c, 3000), 1200, None),
                               (acc(c, 4100), acc(c, 1200), 200, None)]
    assert ledger.balance(c, acc(c, 1200)) == 1000


def test_duplicate_account_in_parts_rejected(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    with pytest.raises(LedgerError, match="doppelt"):
        ledger.book_tx(c, tx, [Part(acc(c, 3000), 1000), Part(acc(c, 3000), 700)],
                       source="manual")


def test_own_bank_as_counter_rejected(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    with pytest.raises(LedgerError, match="Bankkonto"):
        ledger.book_tx(c, tx, [Part(acc(c, 1200), 1700)], source="manual")


def test_cost_center_only_on_income_or_expense(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    cc = ledger.create_cost_center(c, "Jugend")
    with pytest.raises(LedgerError, match="Kostenstelle"):
        ledger.book_tx(c, tx, [Part(acc(c, 1000), 1700, cc)], source="manual")
    pid = ledger.book_tx(c, tx, [Part(acc(c, 3000), 1700, cc)], source="manual")
    assert rows_of(c, pid)[0][3] == cc


def test_hidden_cost_center_not_bookable(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    cc = ledger.create_cost_center(c, "Jugend")
    ledger.set_cost_center_hidden(c, cc, True)
    with pytest.raises(LedgerError, match="Kostenstelle"):
        ledger.book_tx(c, tx, [Part(acc(c, 3000), 1700, cc)], source="manual")


def test_hidden_account_rejected_for_new_but_reversal_ok(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    pid = ledger.book_tx(c, tx, [Part(acc(c, 3100), 1700)], source="manual")
    ledger.set_account_hidden(c, acc(c, 3100), True)
    with pytest.raises(LedgerError, match="ausgeblendet"):
        ledger.book_tx(c, bank_tx(c, 500), [Part(acc(c, 3100), 500)], source="manual")
    rid = ledger.reverse(c, pid, source="manual")
    assert rows_of(c, rid) == [(acc(c, 3100), acc(c, 1200), 1700, None)]


def test_book_tx_creates_missing_period(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700, day="2027-02-01")
    ledger.book_tx(c, tx, [Part(acc(c, 3000), 1700)], source="import")
    names = [r[0] for r in c.execute("select name from period order by start_date")]
    assert names == ["Jahr 2026", "Jahr 2027"]


def test_book_tx_links_bank_account(ledger_conn):
    c = ledger_conn
    tx1, tx2 = bank_tx(c, 100, iban=IBAN1), bank_tx(c, 200, iban=IBAN2)
    ledger.book_tx(c, tx1, [Part(acc(c, 3000), 100)], source="manual")
    p2 = ledger.book_tx(c, tx2, [Part(acc(c, 3000), 200)], source="manual")
    assert rows_of(c, p2)[0][0] == acc(c, 1201)


def test_post_validates(ledger_conn):
    c = ledger_conn
    bank, fees = acc(c, 1200), acc(c, 3000)
    n = count(c, "posting")
    for day, rows in (("2026-03-01", []), ("2026-03-01", [(bank, bank, 100, None)]),
                      ("2026-03-01", [(bank, fees, 0, None)]),
                      ("2026-03-01", [(bank, fees, -5, None)]),
                      ("2026-03-01", [(bank, fees, 5.0, None)]),
                      ("2026-03-01", [(bank, 999, 5, None)]),
                      ("2026-02-30", [(bank, fees, 5, None)]),
                      ("2025-03-01", [(bank, fees, 5, None)])):
        with pytest.raises(LedgerError):
            ledger.post(c, day, rows, source="manual")
    assert count(c, "posting") == n
    pid = ledger.post(c, "2026-03-01", [(acc(c, 1000), bank, 5000, None)], source="manual",
                      text="Bargeld abgehoben", voucher="B-1")
    assert rows_of(c, pid) == [(acc(c, 1000), bank, 5000, None)]


def test_post_is_atomic_on_db_error(ledger_conn, monkeypatch):
    c = ledger_conn
    bank, fees = acc(c, 1200), acc(c, 3000)
    cc = ledger.create_cost_center(c, "Jugend")
    c.commit()
    # Vorprüfung aus: die zweite Zeile scheitert erst am Trigger der Datenbank
    monkeypatch.setattr(ledger, "_check_rows", lambda conn, rows: None)
    with pytest.raises(LedgerError, match="Kostenstellen"):
        ledger.post(c, "2026-03-01", [(bank, fees, 5, None), (acc(c, 1000), bank, 5, cc)],
                    source="manual")
    assert count(c, "posting") == 0 and count(c, "posting_row") == 0


def test_post_does_not_commit(ledger_conn):
    c = ledger_conn
    c.commit()
    ledger.post(c, "2026-03-01", [(acc(c, 1000), acc(c, 1200), 5, None)], source="manual")
    assert c.in_transaction
    c.rollback()
    assert count(c, "posting") == 0


# --- doppelt und Storno ------------------------------------------------------------

def test_same_booking_twice_is_noop(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    p1 = ledger.book_tx(c, tx, [Part(acc(c, 3000), 1000), Part(acc(c, 3100), 700)],
                        source="manual")
    p2 = ledger.book_tx(c, tx, [Part(acc(c, 3100), 700), Part(acc(c, 3000), 1000)],
                        source="rule")
    assert p1 == p2 and count(c, "posting") == 1 and count(c, "posting_row") == 2


def test_same_booking_on_hidden_account_is_noop(ledger_conn):
    """Doppelt gesendetes Formular, Konto inzwischen ausgeblendet: kein Fehler, nichts neu."""
    c = ledger_conn
    tx = bank_tx(c, 1700)
    p1 = ledger.set_counter_account(c, tx, acc(c, 3100), source="manual")
    ledger.set_account_hidden(c, acc(c, 3100), True)
    assert ledger.set_counter_account(c, tx, acc(c, 3100), source="manual") == p1
    assert count(c, "posting") == 1


def test_change_account_reverses_and_rebooks(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    p1 = ledger.set_counter_account(c, tx, acc(c, 3000), source="manual")
    p2 = ledger.set_counter_account(c, tx, acc(c, 3100), source="manual")
    assert p2 != p1 and count(c, "posting") == 3 and valid_count(c, tx) == 1
    assert ledger.valid_posting_id(c, tx) == p2
    assert ledger.balance(c, acc(c, 3000)) == 0
    assert ledger.balance(c, acc(c, 3100)) == -1700
    assert ledger.balance(c, acc(c, 1200)) == 1700


def test_set_none_reverses(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    ledger.set_counter_account(c, tx, acc(c, 3000), source="manual")
    assert ledger.set_counter_account(c, tx, None, source="manual") is None
    assert valid_count(c, tx) == 0 and count(c, "posting") == 2


def test_set_none_on_unbooked_is_noop(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    assert ledger.set_counter_account(c, tx, None, source="manual") is None
    assert ledger.unbook_tx(c, tx, source="manual") is False
    assert count(c, "posting") == 0


def test_reverse_twice_rejected(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    pid = ledger.set_counter_account(c, tx, acc(c, 3000), source="manual")
    rid = ledger.reverse(c, pid, source="manual")
    with pytest.raises(LedgerError, match="schon storniert"):
        ledger.reverse(c, pid, source="manual")
    with pytest.raises(LedgerError, match="Storno"):
        ledger.reverse(c, rid, source="manual")
    with pytest.raises(LedgerError):
        ledger.reverse(c, 999, source="manual")


def test_reversal_keeps_original_date(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700, day="2026-02-14")
    pid = ledger.set_counter_account(c, tx, acc(c, 3000), source="manual")
    rid = ledger.reverse(c, pid, source="manual")
    r = c.execute("select booking_date, tx_id, reversal_of from posting where id=?",
                  (rid,)).fetchone()
    assert tuple(r) == ("2026-02-14", tx, pid)


def test_reverse_in_closed_period_rejected(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    pid = ledger.set_counter_account(c, tx, acc(c, 3000), source="manual")
    close_all(c)
    with pytest.raises(LedgerError, match="abgeschlossen"):
        ledger.reverse(c, pid, source="manual")
    with pytest.raises(LedgerError, match="abgeschlossen"):
        ledger.set_counter_account(c, tx, acc(c, 3100), source="manual")
    assert count(c, "posting") == 1


def test_set_counter_account_with_cost_center(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, -300)
    cc = ledger.create_cost_center(c, "Jugend")
    pid = ledger.set_counter_account(c, tx, acc(c, 4900), cost_center_id=cc, source="manual")
    assert rows_of(c, pid) == [(acc(c, 4900), acc(c, 1200), 300, cc)]
    assert ledger.set_counter_account(c, tx, acc(c, 4900), cost_center_id=cc,
                                      source="manual") == pid
    p2 = ledger.set_counter_account(c, tx, acc(c, 4900), source="manual")
    assert p2 != pid and rows_of(c, p2)[0][3] is None


# --- Beiträge ----------------------------------------------------------------------

def test_book_fees_if_unbooked_only_once(ledger_conn):
    c = ledger_conn
    tx = bank_tx(c, 1700)
    assert ledger.book_fees_if_unbooked(c, tx) is True
    assert ledger.book_fees_if_unbooked(c, tx) is False
    pid = ledger.valid_posting_id(c, tx)
    assert rows_of(c, pid) == [(acc(c, 1200), acc(c, 3000), 1700, None)]
    assert c.execute("select source from posting where id=?", (pid,)).fetchone()[0] == "fee"


def test_book_fees_skips_booked_outgoing_and_closed(ledger_conn):
    c = ledger_conn
    booked = bank_tx(c, 1700)
    ledger.set_counter_account(c, booked, acc(c, 3200), source="manual")
    assert ledger.book_fees_if_unbooked(c, booked) is False
    assert ledger.balance(c, acc(c, 3000)) == 0
    assert ledger.book_fees_if_unbooked(c, bank_tx(c, -500)) is False
    assert ledger.book_fees_if_unbooked(c, bank_tx(c, 0)) is False
    old = bank_tx(c, 1700, day="2025-05-01")
    c.execute("insert into period(name, start_date, end_date, status, closed_at)"
              " values('Jahr 2025','2025-01-01','2025-12-31','closed','2026-01-02T00:00:00')")
    assert ledger.book_fees_if_unbooked(c, old) is False
    assert ledger.valid_posting_id(c, old) is None
