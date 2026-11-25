"""Automatische Verteilung auf Beitragsmonate (#27). Nur erfundene Daten."""
from datetime import date

import pytest

from kassivo import dues
from kassivo.dues import (
    AutoResult, auto_distribute_pending, auto_rows, confirm_allocations, dues_grid,
    month_status, propose_allocations, proposed_tx_count, repropose_pending, review_reason,
    set_check_start, undo_auto,
)
from kassivo.forecast import forecast
from kassivo.matching import assign_member, auto_assign
from kassivo.members import create_member, leave_member
from kassivo.reports import year_summary

FAKE = "XX00FAKE0000000001"
R_AMOUNT = "Betrag passt nicht zu ganzen Monatsbeiträgen"
R_NONE = "Kein offener Monat (Mitglied ausgetreten oder Zahlung ohne fälligen Monat)"
R_REST = "Rest ohne offenen Monat"


def _tx(conn, amount, day="2026-01-05", member_id=None, cp_iban=None, n=[0]):
    n[0] += 1
    conn.execute("insert or ignore into account(id, iban) values(1, 'XX00FAKEKONTO')")
    cur = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, cp_iban,'
        " member_id, dedupe_key) values(1,?,?,?,?,?)",
        (day, amount, cp_iban, member_id, f"auto{n[0]}"))
    conn.commit()
    return cur.lastrowid


def _anna(conn, name="Anna"):
    return create_member(conn, name, "Aktiv", date(2025, 12, 10))  # zahlt ab 2026-01


def _rows(conn, tx_id):
    return [tuple(r) for r in conn.execute(
        "select month, cents, status, source from allocation where tx_id=? order by month, id",
        (tx_id,))]


def _all(conn):
    return sorted(tuple(r) for r in conn.execute(
        "select tx_id, member_id, month, cents, status, source from allocation"))


@pytest.fixture
def anna(conn):
    set_check_start(conn, "2026-01")
    return _anna(conn)


# --- Regel 1: vor dem Prüfungsstart ---------------------------------------------------

def test_payment_before_check_start_gets_no_rows(conn):
    set_check_start(conn, "2026-03")
    m = _anna(conn)
    t = _tx(conn, 1700, day="2026-02-20", member_id=m)
    assert propose_allocations(conn, t) == []
    assert _rows(conn, t) == []
    assert conn.execute('select member_id from "transaction" where id=?', (t,)).fetchone()[0] == m
    assert proposed_tx_count(conn) == 0
    assert review_reason(conn, t) is None


def test_payment_before_check_start_drops_old_proposal_keeps_confirmed(conn):
    set_check_start(conn, "2026-01")
    m = _anna(conn)
    p = _tx(conn, 2000, day="2026-02-02", member_id=m)
    c = _tx(conn, 1700, day="2026-02-03", member_id=m)
    propose_allocations(conn, p)
    confirm_allocations(conn, c, [("2026-02", 1700)])
    assert _rows(conn, p)[0][2] == "proposed"
    set_check_start(conn, "2026-03")
    assert propose_allocations(conn, p) == []
    assert _rows(conn, p) == []
    assert propose_allocations(conn, c) == [("2026-02", 1700)]
    assert _rows(conn, c) == [("2026-02", 1700, "confirmed", "manual")]


def test_payment_at_end_of_month_before_check_start_is_outside(conn):
    set_check_start(conn, "2026-01")
    m = _anna(conn)
    t = _tx(conn, 1700, day="2025-12-30", member_id=m)
    assert propose_allocations(conn, t) == []
    assert month_status(conn, m, "2026-01", date(2026, 1, 20)).status == "OPEN"


# --- Regeln 2 bis 5: eindeutige Fälle laufen von allein ------------------------------------

def test_exact_fee_in_booking_month_auto(conn, anna):
    t = _tx(conn, 1700, day="2026-01-05", member_id=anna)
    assert propose_allocations(conn, t) == [("2026-01", 1700)]
    assert _rows(conn, t) == [("2026-01", 1700, "confirmed", "auto")]


def test_second_payment_same_month_goes_to_next_month(conn, anna):
    t1 = _tx(conn, 1700, day="2026-01-05", member_id=anna)
    t2 = _tx(conn, 1700, day="2026-01-28", member_id=anna)
    propose_allocations(conn, t1)
    assert propose_allocations(conn, t2) == [("2026-02", 1700)]
    assert _rows(conn, t2) == [("2026-02", 1700, "confirmed", "auto")]
    kinds = {r["tx_id"]: r["kind"] for r in auto_rows(conn)}
    assert kinds == {t1: "Normal", t2: "Vorauszahlung"}


def test_34_two_months_auto(conn, anna):
    t = _tx(conn, 3400, day="2026-01-05", member_id=anna)
    assert propose_allocations(conn, t) == [("2026-01", 1700), ("2026-02", 1700)]
    assert {r[2:] for r in _rows(conn, t)} == {("confirmed", "auto")}


def test_yearly_payment_twelve_months_auto(conn, anna):
    t = _tx(conn, 12 * 1700, day="2026-01-05", member_id=anna)
    allocs = propose_allocations(conn, t)
    assert allocs == [(f"2026-{i:02d}", 1700) for i in range(1, 13)]
    assert {r[2:] for r in _rows(conn, t)} == {("confirmed", "auto")}
    assert {s.status for s in dues_grid(conn, 2026, date(2026, 1, 20))[0]["months"]} == {"PAID"}


def test_multiple_limited_to_36_months(conn, anna):
    assert dues.MAX_MONTHS_AHEAD == 36
    t = _tx(conn, 100000, day="2026-01-05", member_id=anna)
    allocs = propose_allocations(conn, t)
    assert len(allocs) == 37 and allocs[0][0] == "2026-01" and allocs[-1][0] == "2029-01"
    assert allocs[-1] == ("2029-01", 100000 - 36 * 1700)
    assert sum(c for _, c in allocs) == 100000
    assert {r[2:] for r in _rows(conn, t)} == {("proposed", "auto")}
    assert review_reason(conn, t) == R_REST


def test_austritt_limits_multiple(conn, anna):
    leave_member(conn, anna, date(2026, 3, 10))
    exact = _tx(conn, 5100, day="2026-01-05", member_id=anna)
    assert propose_allocations(conn, exact) == [
        ("2026-01", 1700), ("2026-02", 1700), ("2026-03", 1700)]
    assert {r[2] for r in _rows(conn, exact)} == {"confirmed"}


def test_austritt_rest_without_month_is_proposal(conn, anna):
    leave_member(conn, anna, date(2026, 3, 10))
    t = _tx(conn, 6800, day="2026-01-05", member_id=anna)
    assert propose_allocations(conn, t) == [
        ("2026-01", 1700), ("2026-02", 1700), ("2026-03", 3400)]
    assert {r[2:] for r in _rows(conn, t)} == {("proposed", "auto")}
    assert review_reason(conn, t) == R_REST


def test_arrears_booking_month_first_then_oldest(conn, anna):
    jan = _tx(conn, 1700, day="2026-01-05", member_id=anna)
    propose_allocations(conn, jan)
    mar = _tx(conn, 3400, day="2026-03-03", member_id=anna)  # Februar fehlt
    assert propose_allocations(conn, mar) == [("2026-02", 1700), ("2026-03", 1700)]
    assert {r[2:] for r in _rows(conn, mar)} == {("confirmed", "auto")}
    assert {r["tx_id"]: r["kind"] for r in auto_rows(conn)}[mar] == "Rückstand"


def test_arrears_booking_month_beats_older_month(conn, anna):
    # 17,00 im März bei offenem Februar: zuerst der Buchungsmonat
    mar = _tx(conn, 1700, day="2026-03-03", member_id=anna)
    assert propose_allocations(conn, mar) == [("2026-03", 1700)]
    assert _rows(conn, mar)[0][2] == "confirmed"
    assert month_status(conn, anna, "2026-02", date(2026, 3, 10)).status == "OVERDUE"


def test_booking_month_paid_goes_to_oldest_open(conn, anna):
    mar = _tx(conn, 1700, day="2026-03-03", member_id=anna)
    propose_allocations(conn, mar)
    second = _tx(conn, 1700, day="2026-03-20", member_id=anna)
    assert propose_allocations(conn, second) == [("2026-01", 1700)]
    assert _rows(conn, second)[0][2:] == ("confirmed", "auto")


def test_booking_month_before_fee_start_goes_to_first_due_month(conn):
    set_check_start(conn, "2025-01")
    m = _anna(conn)  # zahlt ab 2026-01
    t = _tx(conn, 1700, day="2025-12-20", member_id=m)
    assert propose_allocations(conn, t) == [("2026-01", 1700)]
    assert _rows(conn, t)[0][2:] == ("confirmed", "auto")
    assert {r["tx_id"]: r["kind"] for r in auto_rows(conn)}[t] == "Vorauszahlung"


def test_single_partial_payment_auto_then_overdue_then_completed(conn, anna):
    t = _tx(conn, 1000, day="2026-01-05", member_id=anna)
    assert propose_allocations(conn, t) == [("2026-01", 1000)]
    assert _rows(conn, t) == [("2026-01", 1000, "confirmed", "auto")]
    assert month_status(conn, anna, "2026-01", date(2026, 1, 20)).status == "PARTIAL"
    assert month_status(conn, anna, "2026-01", date(2026, 2, 1)).status == "OVERDUE"
    assert {r["tx_id"]: r["kind"] for r in auto_rows(conn)}[t] == "Teilzahlung"
    rest = _tx(conn, 700, day="2026-01-25", member_id=anna)
    assert propose_allocations(conn, rest) == [("2026-01", 700)]
    assert _rows(conn, rest) == [("2026-01", 700, "confirmed", "auto")]
    assert month_status(conn, anna, "2026-01", date(2026, 2, 1)).status == "PAID"


def test_one_cent_is_single_partial(conn, anna):
    t = _tx(conn, 1, day="2026-01-05", member_id=anna)
    assert propose_allocations(conn, t) == [("2026-01", 1)]
    assert _rows(conn, t)[0][2] == "confirmed"


# --- Regel 6: echte Ausnahmen ------------------------------------------------------------

def test_20_euro_is_proposal_with_reason(conn, anna):
    t = _tx(conn, 2000, day="2026-01-05", member_id=anna)
    assert propose_allocations(conn, t) == [("2026-01", 1700), ("2026-02", 300)]
    assert _rows(conn, t) == [("2026-01", 1700, "proposed", "auto"),
                              ("2026-02", 300, "proposed", "auto")]
    assert review_reason(conn, t) == R_AMOUNT
    assert proposed_tx_count(conn) == 1
    assert month_status(conn, anna, "2026-01", date(2026, 1, 20)).paid == 0


def test_payment_after_austritt_without_open_month_is_proposal(conn, anna):
    leave_member(conn, anna, date(2026, 1, 15))
    jan = _tx(conn, 1700, day="2026-01-05", member_id=anna)
    propose_allocations(conn, jan)
    late = _tx(conn, 1700, day="2026-02-03", member_id=anna)
    assert propose_allocations(conn, late) == [("2026-02", 1700)]
    assert _rows(conn, late) == [("2026-02", 1700, "proposed", "auto")]
    assert review_reason(conn, late) == R_NONE


def test_payment_after_austritt_fills_open_month_auto(conn, anna):
    leave_member(conn, anna, date(2026, 1, 15))
    late = _tx(conn, 1700, day="2026-02-03", member_id=anna)  # Januar ist noch offen
    assert propose_allocations(conn, late) == [("2026-01", 1700)]
    assert _rows(conn, late)[0][2] == "confirmed"


def test_rest_added_to_last_month(conn, anna):
    leave_member(conn, anna, date(2026, 2, 10))
    t1 = _tx(conn, 2700, day="2026-01-05", member_id=anna)
    confirm_allocations(conn, t1, [("2026-01", 1700), ("2026-02", 1000)])
    t2 = _tx(conn, 1700, day="2026-02-20", member_id=anna)
    assert propose_allocations(conn, t2) == [("2026-02", 1700)]  # 700 fällig + 1000 Rest
    assert review_reason(conn, t2) == R_REST


def test_review_reason_none_for_clean_or_unknown(conn, anna):
    t = _tx(conn, 1700, day="2026-01-05", member_id=anna)
    propose_allocations(conn, t)
    assert review_reason(conn, t) is None
    assert review_reason(conn, 99999) is None


# --- Regel 9: Szenario aus dem Abschluss-Review -----------------------------------------

def _long_member(conn):
    """Mitglied seit 2015, je 17,00 vom 30.12.2024 bis Dezember 2025 (erfunden)."""
    m = create_member(conn, "Test Person", "Aktiv", date(2015, 3, 1), iban=FAKE)
    ids = [_tx(conn, 1700, day="2024-12-30", cp_iban=FAKE)]
    ids += [_tx(conn, 1700, day=f"2025-{i:02d}-05", cp_iban=FAKE) for i in range(1, 13)]
    return m, ids


def _assert_long_member_clean(conn, m, ids):
    for t in ids:
        booking = conn.execute('select booking_date from "transaction" where id=?',
                               (t,)).fetchone()[0]
        assert _rows(conn, t) == [(booking[:7], 1700, "confirmed", "auto")]
    today = date(2026, 1, 10)
    assert [s.status for s in dues_grid(conn, 2025, today)[0]["months"]] == ["PAID"] * 12
    assert [s.status for s in dues_grid(conn, 2024, today)[0]["months"]] == ["NONE"] * 11 + ["PAID"]
    assert year_summary(conn, 2024)["by_member"] == [{"name": "Test Person", "paid_cents": 1700}]
    assert year_summary(conn, 2025)["by_member"] == [{"name": "Test Person",
                                                      "paid_cents": 12 * 1700}]
    fc = forecast(conn, date(2025, 12, 31), today)
    assert (fc.overdue_cents, fc.due_cents, fc.items) == (0, 0, [])
    assert proposed_tx_count(conn) == 0


def test_long_member_each_payment_in_own_month(conn):
    m, ids = _long_member(conn)
    assert auto_assign(conn, ids) == 13
    _assert_long_member_clean(conn, m, ids)


def test_long_member_newest_first_same_result(conn):
    m, ids = _long_member(conn)
    for t in reversed(ids):
        assign_member(conn, t, m, learn=False)
    _assert_long_member_clean(conn, m, ids)


def test_long_member_explicit_check_start_drops_earlier_payment(conn):
    set_check_start(conn, "2025-01")
    m, ids = _long_member(conn)
    auto_assign(conn, ids)
    assert _rows(conn, ids[0]) == []
    for i, t in enumerate(ids[1:], start=1):
        assert _rows(conn, t) == [(f"2025-{i:02d}", 1700, "confirmed", "auto")]
    assert proposed_tx_count(conn) == 0


# --- Regel 8: source, Rückgängig ---------------------------------------------------------

def test_confirm_allocations_is_manual(conn, anna):
    t = _tx(conn, 2000, day="2026-01-05", member_id=anna)
    propose_allocations(conn, t)
    confirm_allocations(conn, t, [("2026-01", 2000)])
    assert _rows(conn, t) == [("2026-01", 2000, "confirmed", "manual")]
    assert auto_rows(conn) == []


def test_auto_rows_content(conn, anna):
    t = _tx(conn, 3400, day="2026-01-05", member_id=anna)
    conn.execute('update "transaction" set cp_name=? where id=?', ("Anna Muster", t))
    conn.commit()
    propose_allocations(conn, t)
    assert auto_rows(conn) == [{
        "tx_id": t, "booking_date": "2026-01-05", "amount_cents": 3400,
        "cp_name": "Anna Muster", "member_id": anna, "member_name": "Anna",
        "allocations": [("2026-01", 1700), ("2026-02", 1700)], "kind": "Vorauszahlung"}]


def test_auto_rows_newest_first_and_limit(conn, anna):
    ids = [_tx(conn, 1700, day=f"2026-{i:02d}-05", member_id=anna) for i in range(1, 4)]
    for t in ids:
        propose_allocations(conn, t)
    assert [r["tx_id"] for r in auto_rows(conn)] == list(reversed(ids))
    assert [r["tx_id"] for r in auto_rows(conn, limit=2)] == [ids[2], ids[1]]


def test_undo_auto_makes_manual_proposal_not_reconfirmed(conn, anna):
    t = _tx(conn, 1700, day="2026-01-05", member_id=anna)
    propose_allocations(conn, t)
    undo_auto(conn, t)
    assert not conn.in_transaction
    assert _rows(conn, t) == [("2026-01", 1700, "proposed", "manual")]
    assert proposed_tx_count(conn) == 1
    assert month_status(conn, anna, "2026-01", date(2026, 1, 20)).status == "OPEN"
    assert auto_rows(conn) == []
    assert auto_distribute_pending(conn) == AutoResult(0, 0, 0, 0)
    set_check_start(conn, "2026-01")
    assert repropose_pending(conn) == 0
    assert _rows(conn, t) == [("2026-01", 1700, "proposed", "manual")]
    assert review_reason(conn, t) is not None


def test_undo_auto_refuses_manual_or_unknown(conn, anna):
    t = _tx(conn, 1700, day="2026-01-05", member_id=anna)
    confirm_allocations(conn, t, [("2026-01", 1700)])
    before = _all(conn)
    with pytest.raises(ValueError):
        undo_auto(conn, t)
    with pytest.raises(ValueError):
        undo_auto(conn, 99999)
    p = _tx(conn, 2000, day="2026-02-05", member_id=anna)
    propose_allocations(conn, p)  # nur Vorschlag
    with pytest.raises(ValueError):
        undo_auto(conn, p)
    assert [r for r in _all(conn) if r[0] == t] == [r for r in before if r[0] == t]
    assert not conn.in_transaction


def test_automatic_runs_never_touch_manual_confirmation(conn, anna):
    t = _tx(conn, 1700, day="2026-01-05", member_id=anna)
    confirm_allocations(conn, t, [("2026-03", 1700)])
    before = _all(conn)
    auto_distribute_pending(conn)
    set_check_start(conn, "2026-02")
    repropose_pending(conn)
    assert _all(conn) == before


# --- auto_distribute_pending -------------------------------------------------------------

def test_auto_distribute_counts(conn):
    set_check_start(conn, "2026-02")
    m = _anna(conn)
    outside = _tx(conn, 1700, day="2026-01-05", member_id=m)
    ok = _tx(conn, 1700, day="2026-02-05", member_id=m)
    odd = _tx(conn, 2000, day="2026-03-05", member_id=m)
    # Fix-Runde 1 (f): Zahlungen vor dem Prüfungsstart ohne Zeilen zählen nicht mit
    assert auto_distribute_pending(conn) == AutoResult(total=2, confirmed=1, review=1, outside=0)
    assert not conn.in_transaction
    assert _rows(conn, outside) == []
    assert _rows(conn, ok) == [("2026-02", 1700, "confirmed", "auto")]
    assert _rows(conn, odd) == [("2026-03", 1700, "proposed", "auto"),
                                ("2026-04", 300, "proposed", "auto")]


def test_auto_distribute_turns_old_proposals_into_auto(conn):
    # Altbestand: Vorschläge nach der alten Regel (z. B. 34,00), source auto
    set_check_start(conn, "2026-01")
    m = _anna(conn)
    t = _tx(conn, 3400, day="2026-01-05", member_id=m)
    conn.execute("insert into allocation(tx_id, member_id, month, cents, status, source)"
                 " values(?,?,?,?,'proposed','auto')", (t, m, "2026-01", 3400))
    conn.commit()
    assert repropose_pending(conn) == 1
    assert _rows(conn, t) == [("2026-01", 1700, "confirmed", "auto"),
                              ("2026-02", 1700, "confirmed", "auto")]


def test_auto_distribute_chronological_later_sees_earlier(conn, anna):
    a = _tx(conn, 1700, day="2026-01-05", member_id=anna)
    b = _tx(conn, 1700, day="2026-01-20", member_id=anna)
    assert auto_distribute_pending(conn).confirmed == 2
    assert _rows(conn, a) == [("2026-01", 1700, "confirmed", "auto")]
    assert _rows(conn, b) == [("2026-02", 1700, "confirmed", "auto")]


def test_auto_distribute_idempotent(conn, anna):
    other = _anna(conn, "Bert")
    for m in (anna, other):
        _tx(conn, 1700, day="2026-01-05", member_id=m)
        _tx(conn, 2000, day="2026-02-05", member_id=m)
        _tx(conn, 1000, day="2026-03-05", member_id=m)
    auto_distribute_pending(conn)
    first = _all(conn)
    res = auto_distribute_pending(conn)
    assert _all(conn) == first
    assert res == AutoResult(total=2, confirmed=0, review=2, outside=0)


def _two_members(conn, order):
    set_check_start(conn, "2026-01")
    a, b = _anna(conn, "A"), _anna(conn, "B")
    txs = {a: [_tx(conn, c, day=d, member_id=a) for c, d in
               ((1700, "2026-01-05"), (3400, "2026-02-05"), (2000, "2026-03-05"))],
           b: [_tx(conn, c, day=d, member_id=b) for c, d in
               ((1000, "2026-01-05"), (1700, "2026-01-25"))]}
    for m in order(a, b):
        for t in txs[m]:
            propose_allocations(conn, t)
    return sorted((r[1], r[2:]) for r in _all(conn))


def test_independent_members_order_irrelevant(conn):
    from kassivo.db import connect
    other = connect(":memory:")
    try:
        assert _two_members(conn, lambda a, b: (a, b)) == _two_members(other, lambda a, b: (b, a))
    finally:
        other.close()


def test_auto_distribute_atomic_on_error(conn, anna, monkeypatch):
    ids = [_tx(conn, 1700, day=f"2026-{i:02d}-05", member_id=anna) for i in range(1, 5)]
    before = _all(conn)
    real, calls = dues.propose_allocations, []

    def flaky(c, tx_id, **kw):
        calls.append(tx_id)
        if len(calls) == 3:
            raise RuntimeError("Testfehler")
        return real(c, tx_id, **kw)

    monkeypatch.setattr(dues, "propose_allocations", flaky)
    with pytest.raises(RuntimeError):
        auto_distribute_pending(conn)
    assert calls[:2] == ids[:2]
    assert _all(conn) == before == []
    assert not conn.in_transaction


def test_propose_auto_false_stores_manual_proposal(conn, anna):
    t = _tx(conn, 1700, day="2026-01-05", member_id=anna)
    assert propose_allocations(conn, t, auto=False) == [("2026-01", 1700)]
    assert _rows(conn, t) == [("2026-01", 1700, "proposed", "manual")]


def test_propose_commit_false_outside_period(conn):
    set_check_start(conn, "2026-03")
    m = _anna(conn)
    t = _tx(conn, 1700, day="2026-02-05", member_id=m)
    conn.execute("insert into allocation(tx_id, member_id, month, cents, status, source)"
                 " values(?,?,?,?,'proposed','auto')", (t, m, "2026-03", 1700))
    conn.commit()
    propose_allocations(conn, t, commit=False)
    conn.rollback()
    assert len(_rows(conn, t)) == 1


# --- Regel 12: bestehende Abläufe lösen die Verteilung aus --------------------------------

def test_assign_member_distributes_automatically(conn, anna):
    t = _tx(conn, 3400, day="2026-01-05", cp_iban=FAKE)
    assign_member(conn, t, anna)
    assert {r[2:] for r in _rows(conn, t)} == {("confirmed", "auto")}


def test_auto_assign_distributes_automatically(conn, anna):
    conn.execute("update member set iban=? where id=?", (FAKE, anna))
    conn.commit()
    t = _tx(conn, 1000, day="2026-01-05", cp_iban=FAKE)
    assert auto_assign(conn, [t]) == 1
    assert _rows(conn, t) == [("2026-01", 1000, "confirmed", "auto")]


def test_reassign_auto_confirmed_recomputes_for_new_member(conn, anna):
    bert = _anna(conn, "Bert")
    t = _tx(conn, 1700, day="2026-01-05", cp_iban=FAKE)
    assign_member(conn, t, anna, learn=False)
    assert _rows(conn, t) == [("2026-01", 1700, "confirmed", "auto")]
    assign_member(conn, t, bert, learn=False)
    assert conn.execute("select distinct member_id from allocation where tx_id=?",
                        (t,)).fetchall()[0][0] == bert
    assert _rows(conn, t) == [("2026-01", 1700, "confirmed", "auto")]
    assert month_status(conn, anna, "2026-01", date(2026, 1, 20)).status == "OPEN"


def test_reassign_manual_confirmation_still_blocked(conn, anna):
    bert = _anna(conn, "Bert")
    t = _tx(conn, 1700, day="2026-01-05", cp_iban=FAKE)
    assign_member(conn, t, anna, learn=False)
    confirm_allocations(conn, t, [("2026-01", 1700)])
    with pytest.raises(ValueError, match="Verteilung zuerst aufheben"):
        assign_member(conn, t, bert, learn=False)
    assert _rows(conn, t) == [("2026-01", 1700, "confirmed", "manual")]


def test_siblings_second_payment_can_be_moved_to_other_child(conn, anna):
    from kassivo.matching import iban_is_ambiguous
    bert = _anna(conn, "Bert")
    t1 = _tx(conn, 1700, day="2026-01-05", cp_iban=FAKE)
    t2 = _tx(conn, 1700, day="2026-01-05", cp_iban=FAKE)
    assign_member(conn, t1, anna)            # lernt die Eltern-IBAN für Anna
    assert auto_assign(conn, [t2]) == 1      # zweite Zahlung: Anna, Vorauszahlung Februar
    assert _rows(conn, t2) == [("2026-02", 1700, "confirmed", "auto")]
    assign_member(conn, t2, bert)            # Korrektur: gehört zu Bert
    assert _rows(conn, t2) == [("2026-01", 1700, "confirmed", "auto")]
    assert iban_is_ambiguous(conn, FAKE)
