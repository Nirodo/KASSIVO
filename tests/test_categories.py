"""Regeln auf Konten (Schema 4): buchen über das Journal."""
import pytest

from kassivo import ledger, periods
from kassivo.categories import add_rule, apply_rules


def _acc(c, number):
    return c.execute("select id from ledger_account where number = ?", (number,)).fetchone()[0]


def _tx(c, amount, purpose=None, cp_name=None, cp_iban=None, day="2026-01-01", n=[0]):
    n[0] += 1
    c.execute("insert or ignore into account(id, iban) values(1, 'DE00TEST')")
    return c.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, cp_name,'
        " cp_iban, purpose, dedupe_key) values(1,?,?,?,?,?,?)",
        (day, amount, cp_name, cp_iban, purpose, f"k{n[0]}")).lastrowid


def _counter(c, tx_id):
    found = ledger.counter_accounts(c, [tx_id]).get(tx_id)
    return found[0] if found else None


def _postings(c, tx_id):
    return c.execute("select count(*) from posting where tx_id = ?", (tx_id,)).fetchone()[0]


def test_rule_books_matching_tx(conn):
    miete = ledger.create_account(conn, "Miete", "expense")
    add_rule(conn, miete, "purpose", "miete")
    t = _tx(conn, -5000, purpose="MIETE Halle Nov")
    assert apply_rules(conn) == 1
    assert _counter(conn, t) == miete
    p = ledger.valid_posting_id(conn, t)
    assert conn.execute("select source from posting where id = ?", (p,)).fetchone()[0] == "rule"


def test_umlaut_casefold_and_iban_spaces(conn):
    spenden = _acc(conn, 3100)
    add_rule(conn, spenden, "cp_name", "MÜLLER")
    add_rule(conn, spenden, "cp_iban", "de89 3704")
    t1 = _tx(conn, 100, cp_name="Hans müller")
    t2 = _tx(conn, 100, cp_iban="DE89370400440532013000")
    assert apply_rules(conn) == 2
    assert _counter(conn, t1) == _counter(conn, t2) == spenden


def test_income_rule_ignores_outgoing(conn):
    add_rule(conn, _acc(conn, 3100), "purpose", "x")
    t = _tx(conn, -100, purpose="x")
    assert apply_rules(conn) == 0
    assert _postings(conn, t) == 0


def test_expense_rule_ignores_incoming(conn):
    add_rule(conn, _acc(conn, 4000), "purpose", "x")
    t = _tx(conn, 100, purpose="x")
    assert apply_rules(conn) == 0
    assert _postings(conn, t) == 0


def test_zero_amount_never_booked(conn):
    add_rule(conn, _acc(conn, 3100), "purpose", "x")
    add_rule(conn, _acc(conn, 4000), "purpose", "x")
    t = _tx(conn, 0, purpose="x")
    assert apply_rules(conn) == 0
    assert _postings(conn, t) == 0


def test_priority_order(conn):
    a = ledger.create_account(conn, "A", "expense")
    b = ledger.create_account(conn, "B", "expense")
    add_rule(conn, a, "purpose", "halle", priority=50)
    add_rule(conn, b, "purpose", "halle", priority=10)
    add_rule(conn, a, "purpose", "tie")
    add_rule(conn, b, "purpose", "tie")
    t1 = _tx(conn, -100, purpose="Halle")
    t2 = _tx(conn, -100, purpose="tie")
    apply_rules(conn)
    assert _counter(conn, t1) == b
    assert _counter(conn, t2) == a  # gleiche Priorität: kleinere Regel-ID


def test_amount_limits_inclusive_abs(conn):
    add_rule(conn, _acc(conn, 3900), "purpose", "x", min_cents=1000, max_cents=2000)
    add_rule(conn, _acc(conn, 4900), "purpose", "x", min_cents=1000, max_cents=2000)
    t1, t2 = _tx(conn, 1700, purpose="x"), _tx(conn, 2500, purpose="x")
    t3, t4 = _tx(conn, -2000, purpose="x"), _tx(conn, 999, purpose="x")
    assert apply_rules(conn) == 2
    assert _counter(conn, t1) == _acc(conn, 3900)
    assert _counter(conn, t3) == _acc(conn, 4900)
    assert _postings(conn, t2) == _postings(conn, t4) == 0


def test_none_field_no_crash(conn):
    add_rule(conn, _acc(conn, 3100), "cp_name", "x")
    t = _tx(conn, 100, cp_name=None)
    assert apply_rules(conn) == 0
    assert _postings(conn, t) == 0


def test_booked_kept_without_overwrite(conn):
    a = ledger.create_account(conn, "A", "expense")
    m = ledger.create_account(conn, "M", "expense")
    add_rule(conn, a, "purpose", "halle")
    t = _tx(conn, -100, purpose="Halle")
    ledger.set_counter_account(conn, t, m, source="manual")
    assert apply_rules(conn) == 0
    assert _counter(conn, t) == m


def test_overwrite_reverses_and_rebooks(conn):
    a = ledger.create_account(conn, "A", "expense")
    m = ledger.create_account(conn, "M", "expense")
    add_rule(conn, a, "purpose", "halle")
    t = _tx(conn, -100, purpose="Halle")
    ledger.set_counter_account(conn, t, m, source="manual")
    assert apply_rules(conn, overwrite=True) == 1
    assert _counter(conn, t) == a
    assert _postings(conn, t) == 3  # Original, Storno, neu
    assert conn.execute("select count(*) from posting where tx_id = ? and reversal_of"
                         " is not null", (t,)).fetchone()[0] == 1


def test_same_account_counts_zero(conn):
    a = ledger.create_account(conn, "A", "expense")
    add_rule(conn, a, "purpose", "halle")
    t = _tx(conn, -100, purpose="Halle")
    assert apply_rules(conn) == 1
    assert apply_rules(conn, overwrite=True) == 0
    assert _postings(conn, t) == 1


def test_tx_ids_subset(conn):
    add_rule(conn, _acc(conn, 3100), "purpose", "x")
    t1, t2 = _tx(conn, 100, purpose="x"), _tx(conn, 100, purpose="x")
    assert apply_rules(conn, [t1]) == 1
    assert _postings(conn, t2) == 0


def test_hidden_account_rule_sleeps(conn):
    a = ledger.create_account(conn, "A", "expense")
    b = ledger.create_account(conn, "B", "expense")
    add_rule(conn, a, "purpose", "halle", priority=1)
    add_rule(conn, b, "purpose", "halle", priority=2)
    ledger.set_account_hidden(conn, a, True)
    t = _tx(conn, -100, purpose="Halle")
    assert apply_rules(conn) == 1
    assert _counter(conn, t) == b  # ruhende Regel zählt nicht, nächste greift


def test_closed_period_skipped(conn):
    add_rule(conn, _acc(conn, 3100), "purpose", "x")
    t = _tx(conn, 100, purpose="x", day="2026-03-01")
    periods.ensure_period(conn, "2026-03-01")
    conn.execute("update period set status = 'closed', closed_at = '2026-12-31T00:00:00'"
                  " where '2026-03-01' between start_date and end_date")
    assert apply_rules(conn) == 0
    assert _postings(conn, t) == 0


def test_missing_period_created(conn):
    add_rule(conn, _acc(conn, 3100), "purpose", "x")
    t = _tx(conn, 100, purpose="x", day="2019-05-01")
    assert apply_rules(conn) == 1
    assert _postings(conn, t) == 1


def test_add_rule_rejects_asset_hidden_unknown_and_bad_input(conn):
    a = ledger.create_account(conn, "A", "expense")
    ledger.set_account_hidden(conn, a, True)
    for account_id in (_acc(conn, 1200), _acc(conn, 2000), a, 9999):
        with pytest.raises(ValueError):
            add_rule(conn, account_id, "purpose", "x")
    with pytest.raises(ValueError):
        add_rule(conn, _acc(conn, 3100), "amount_cents; drop", "x")
    with pytest.raises(ValueError):
        add_rule(conn, _acc(conn, 3100), "purpose", "")
    assert conn.execute("select count(*) from posting_rule").fetchone()[0] == 0


def test_apply_does_not_commit(conn):
    add_rule(conn, _acc(conn, 3100), "purpose", "x")
    conn.commit()
    t = _tx(conn, 100, purpose="x")
    apply_rules(conn)
    conn.rollback()
    assert conn.execute('select count(*) from "transaction" where id = ?', (t,)).fetchone()[0] == 0
    assert conn.execute("select count(*) from posting").fetchone()[0] == 0
