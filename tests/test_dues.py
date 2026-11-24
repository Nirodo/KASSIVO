"""Beitragsprüfung: Status, Verteilungsvorschlag, Historie. Nur erfundene Daten."""
from datetime import date

import sqlite3

import pytest

from kassivo.db import set_setting
from kassivo.dues import (
    MonthStatus, check_start_is_set, clear_allocations, confirm_allocations, dues_grid, get_check_start, month_status,
    payment_history, propose_allocations, proposed_months, proposed_tx_count, set_check_start,
)
from kassivo.fees import set_fee
from kassivo.matching import assign_member, auto_assign, unmatched
from kassivo.reports import year_summary
from kassivo.members import create_member, leave_member

MOM = "DE22222222222222222222"


def _tx(conn, amount, day="2026-01-05", member_id=None, cp_name=None,
        cp_iban=None, purpose=None, n=[0]):
    n[0] += 1
    conn.execute("insert or ignore into account(id, iban) values(1, 'DE00TEST')")
    cur = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents,'
        " cp_name, cp_iban, purpose, member_id, dedupe_key)"
        " values(1,?,?,?,?,?,?,?)",
        (day, amount, cp_name, cp_iban, purpose, member_id, f"d{n[0]}"),
    )
    conn.commit()
    return cur.lastrowid


def _anna(conn, name="Anna", type_name="Aktiv"):
    # Eintritt Dezember 2025 -> zahlt ab 2026-01
    return create_member(conn, name, type_name, date(2025, 12, 10))


def _rows(conn, tx_id):
    return [
        (r["month"], r["cents"], r["status"], r["member_id"])
        for r in conn.execute(
            "select month, cents, status, member_id from allocation"
            " where tx_id=? order by month, id", (tx_id,))
    ]


JAN = date(2026, 1, 20)


# --- Vorschlag und Status -------------------------------------------------

def test_exact_17_confirmed_and_paid(conn):
    m = _anna(conn)
    t = _tx(conn, 1700, member_id=m)
    assert propose_allocations(conn, t) == [("2026-01", 1700)]
    assert _rows(conn, t) == [("2026-01", 1700, "confirmed", m)]
    assert month_status(conn, m, "2026-01", JAN) == MonthStatus(
        "2026-01", 1700, 1700, "PAID")


def test_34_two_proposed_not_paid_until_confirmed(conn):
    m = _anna(conn)
    t = _tx(conn, 3400, member_id=m)
    allocs = propose_allocations(conn, t)
    assert allocs == [("2026-01", 1700), ("2026-02", 1700)]
    assert _rows(conn, t) == [
        ("2026-01", 1700, "proposed", m), ("2026-02", 1700, "proposed", m)]
    assert month_status(conn, m, "2026-01", JAN) == MonthStatus(
        "2026-01", 1700, 0, "OPEN")
    assert month_status(conn, m, "2026-02", JAN).status == "OPEN"
    confirm_allocations(conn, t, allocs)
    assert month_status(conn, m, "2026-01", JAN).status == "PAID"
    assert month_status(conn, m, "2026-02", JAN) == MonthStatus(
        "2026-02", 1700, 1700, "PAID")


def test_10_on_17_proposed_then_partial_then_overdue(conn):
    m = _anna(conn)
    t = _tx(conn, 1000, member_id=m)
    assert propose_allocations(conn, t) == [("2026-01", 1000)]
    assert _rows(conn, t) == [("2026-01", 1000, "proposed", m)]
    assert month_status(conn, m, "2026-01", JAN) == MonthStatus(
        "2026-01", 1700, 0, "OPEN")
    confirm_allocations(conn, t, [("2026-01", 1000)])
    assert month_status(conn, m, "2026-01", JAN) == MonthStatus(
        "2026-01", 1700, 1000, "PARTIAL")
    assert month_status(conn, m, "2026-01", date(2026, 2, 1)) == MonthStatus(
        "2026-01", 1700, 1000, "OVERDUE")


def test_unpaid_past_month_overdue_current_open(conn):
    m = _anna(conn)
    today = date(2026, 3, 15)
    assert month_status(conn, m, "2026-02", today) == MonthStatus(
        "2026-02", 1700, 0, "OVERDUE")
    assert month_status(conn, m, "2026-03", today) == MonthStatus(
        "2026-03", 1700, 0, "OPEN")


def test_month_end_is_not_yet_overdue(conn):
    m = _anna(conn)
    assert month_status(conn, m, "2026-01", date(2026, 1, 31)).status == "OPEN"
    assert month_status(conn, m, "2026-01", date(2026, 2, 1)).status == "OVERDUE"
    # Februar im Nicht-Schaltjahr
    assert month_status(conn, m, "2026-02", date(2026, 2, 28)).status == "OPEN"
    assert month_status(conn, m, "2026-02", date(2026, 3, 1)).status == "OVERDUE"


def test_before_fee_start_none(conn):
    m = _anna(conn)
    set_check_start(conn, "2025-01")
    assert month_status(conn, m, "2025-12", JAN) == MonthStatus(
        "2025-12", 0, 0, "NONE")


def test_overpaid_month_counts_as_paid(conn):
    m = _anna(conn)
    t = _tx(conn, 2000, member_id=m)
    confirm_allocations(conn, t, [("2026-01", 2000)])
    assert month_status(conn, m, "2026-01", JAN) == MonthStatus(
        "2026-01", 1700, 2000, "PAID")


def test_fee_raise_keeps_old_months(conn):
    m = _anna(conn)
    set_check_start(conn, "2026-12")
    t1 = _tx(conn, 1700, day="2026-12-03", member_id=m)
    assert propose_allocations(conn, t1) == [("2026-12", 1700)]
    set_fee(conn, "Aktiv", 2000, "2027-01")
    today = date(2027, 1, 10)
    assert month_status(conn, m, "2026-12", today) == MonthStatus(
        "2026-12", 1700, 1700, "PAID")
    assert month_status(conn, m, "2027-01", today) == MonthStatus(
        "2027-01", 2000, 0, "OPEN")
    t2 = _tx(conn, 1700, day="2027-01-04", member_id=m)
    assert propose_allocations(conn, t2) == [("2027-01", 1700)]
    assert _rows(conn, t2)[0][2] == "proposed"  # 1700 deckt 2000 nicht
    confirm_allocations(conn, t2, [("2027-01", 1700)])
    assert month_status(conn, m, "2027-01", today).status == "PARTIAL"
    t3 = _tx(conn, 2000, day="2027-02-02", member_id=m)
    # Januar fehlt noch 300, also kein exakter Treffer
    assert propose_allocations(conn, t3) == [("2027-01", 300), ("2027-02", 1700)]


def test_fee_raise_exact_2000_confirmed(conn):
    m = _anna(conn)
    set_check_start(conn, "2027-01")
    set_fee(conn, "Aktiv", 2000, "2027-01")
    t = _tx(conn, 2000, day="2027-01-04", member_id=m)
    assert propose_allocations(conn, t) == [("2027-01", 2000)]
    assert _rows(conn, t) == [("2027-01", 2000, "confirmed", m)]


def test_austritt_12_oct_october_due_november_none(conn):
    m = _anna(conn)
    leave_member(conn, m, date(2026, 10, 12))
    today = date(2026, 11, 24)
    assert month_status(conn, m, "2026-10", today) == MonthStatus(
        "2026-10", 1700, 0, "OVERDUE")
    assert month_status(conn, m, "2026-11", today) == MonthStatus(
        "2026-11", 0, 0, "NONE")


def test_passiv_member_exact_1000(conn):
    m = _anna(conn, "Paul", "Passiv")
    t = _tx(conn, 1000, member_id=m)
    assert propose_allocations(conn, t) == [("2026-01", 1000)]
    assert _rows(conn, t)[0][2] == "confirmed"


def test_second_payment_fills_next_month(conn):
    m = _anna(conn)
    t1 = _tx(conn, 1700, day="2026-01-05", member_id=m)
    t2 = _tx(conn, 1700, day="2026-02-03", member_id=m)
    propose_allocations(conn, t1)
    assert propose_allocations(conn, t2) == [("2026-02", 1700)]
    assert _rows(conn, t2)[0][2] == "confirmed"


def test_rest_of_partly_paid_month_is_exact(conn):
    m = _anna(conn)
    t1 = _tx(conn, 1000, member_id=m)
    confirm_allocations(conn, t1, [("2026-01", 1000)])
    t2 = _tx(conn, 700, day="2026-01-20", member_id=m)
    assert propose_allocations(conn, t2) == [("2026-01", 700)]
    assert _rows(conn, t2)[0][2] == "confirmed"
    assert month_status(conn, m, "2026-01", JAN).status == "PAID"


def test_catch_up_skips_paid_months(conn):
    set_check_start(conn, "2026-01")  # Standard wäre der Monat der ältesten Buchung
    m = _anna(conn)
    t1 = _tx(conn, 1700, day="2026-03-02", member_id=m)
    confirm_allocations(conn, t1, [("2026-03", 1700)])
    t2 = _tx(conn, 5100, day="2026-04-02", member_id=m)
    assert propose_allocations(conn, t2) == [
        ("2026-01", 1700), ("2026-02", 1700), ("2026-04", 1700)]


def test_proposed_rows_of_other_tx_do_not_count(conn):
    m = _anna(conn)
    t1 = _tx(conn, 3400, member_id=m)
    propose_allocations(conn, t1)  # bleibt proposed
    t2 = _tx(conn, 1700, day="2026-01-06", member_id=m)
    assert propose_allocations(conn, t2) == [("2026-01", 1700)]


def test_leftover_member_left_goes_to_booking_month(conn):
    m = _anna(conn)
    leave_member(conn, m, date(2026, 2, 10))
    t1 = _tx(conn, 3400, day="2026-01-05", member_id=m)
    confirm_allocations(conn, t1, [("2026-01", 1700), ("2026-02", 1700)])
    t2 = _tx(conn, 1700, day="2026-04-01", member_id=m)
    allocs = propose_allocations(conn, t2)
    assert allocs == [("2026-04", 1700)]
    assert sum(c for _, c in allocs) == 1700
    assert _rows(conn, t2) == [("2026-04", 1700, "proposed", m)]


def test_leftover_added_to_last_allocated_month(conn):
    m = _anna(conn)
    leave_member(conn, m, date(2026, 2, 10))
    t1 = _tx(conn, 2700, day="2026-01-05", member_id=m)
    confirm_allocations(conn, t1, [("2026-01", 1700), ("2026-02", 1000)])
    t2 = _tx(conn, 1700, day="2026-02-20", member_id=m)
    allocs = propose_allocations(conn, t2)
    assert allocs == [("2026-02", 1700)]  # 700 fällig + 1000 Rest, eine Zeile
    assert _rows(conn, t2) == [("2026-02", 1700, "proposed", m)]


def test_large_overpayment_capped_at_24_months(conn):
    m = _anna(conn)
    t = _tx(conn, 100000, day="2026-01-05", member_id=m)
    allocs = propose_allocations(conn, t)
    months = [mo for mo, _ in allocs]
    assert months[0] == "2026-01" and months[-1] == "2028-01"
    assert len(months) == 25 == len(set(months))
    assert sum(c for _, c in allocs) == 100000
    assert allocs[-1] == ("2028-01", 100000 - 24 * 1700)
    assert all(isinstance(c, int) for _, c in allocs)
    assert {r[2] for r in _rows(conn, t)} == {"proposed"}


def test_one_cent(conn):
    m = _anna(conn)
    t = _tx(conn, 1, member_id=m)
    assert propose_allocations(conn, t) == [("2026-01", 1)]
    assert _rows(conn, t)[0][2] == "proposed"


def test_proposals_idempotent(conn):
    m = _anna(conn)
    t = _tx(conn, 3400, member_id=m)
    first = propose_allocations(conn, t)
    second = propose_allocations(conn, t)
    assert first == second
    assert len(_rows(conn, t)) == 2


def test_confirmed_rows_untouched_by_propose(conn):
    m = _anna(conn)
    t = _tx(conn, 3400, member_id=m)
    confirm_allocations(conn, t, [("2026-03", 3400)])
    before = conn.execute(
        "select id, month, cents, status from allocation where tx_id=?", (t,)
    ).fetchall()
    assert propose_allocations(conn, t) == [("2026-03", 3400)]
    after = conn.execute(
        "select id, month, cents, status from allocation where tx_id=?", (t,)
    ).fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]


@pytest.mark.parametrize("amount", [0, -1700])
def test_propose_rejects_non_incoming(conn, amount):
    m = _anna(conn)
    t = _tx(conn, amount, member_id=m)
    with pytest.raises(ValueError):
        propose_allocations(conn, t)
    assert _rows(conn, t) == []


def test_propose_rejects_without_member_or_unknown_tx(conn):
    t = _tx(conn, 1700)
    with pytest.raises(ValueError):
        propose_allocations(conn, t)
    with pytest.raises(ValueError):
        propose_allocations(conn, 9999)


def test_propose_commit_false_leaves_commit_to_caller(conn):
    m = _anna(conn)
    t = _tx(conn, 3400, member_id=m)
    propose_allocations(conn, t, commit=False)
    conn.rollback()
    assert _rows(conn, t) == []
    propose_allocations(conn, t)
    conn.rollback()
    assert len(_rows(conn, t)) == 2


# --- Bestätigen -----------------------------------------------------------

@pytest.mark.parametrize("allocs", [
    [("2026-01", 1700)],                       # Summe zu klein
    [("2026-01", 1700), ("2026-02", 1701)],    # Summe zu groß
    [],                                        # leer
    [("2026-01", 3400), ("2026-02", 0)],       # 0 Cent
    [("2026-01", 3500), ("2026-02", -100)],    # negativ
    [("2026-01", 1700), ("2026-01", 1700)],    # doppelter Monat
    [("2026-13", 3400)],                       # kein Monat
    [("2026-1", 3400)],                        # Format
    [("2026-01-01", 3400)],                    # Datum statt Monat
    [("2026-01", 1700.0), ("2026-02", 1700)],  # Float
    [("2026-01", True), ("2026-02", 3399)],    # bool ist kein Betrag
    [(202601, 3400)],                          # Monat kein String
])
def test_confirm_invalid_changes_nothing(conn, allocs):
    m = _anna(conn)
    t = _tx(conn, 3400, member_id=m)
    propose_allocations(conn, t)
    before = _rows(conn, t)
    with pytest.raises(ValueError):
        confirm_allocations(conn, t, allocs)
    assert _rows(conn, t) == before


def test_confirm_rejects_non_incoming_or_no_member(conn):
    m = _anna(conn)
    out = _tx(conn, -1700, member_id=m)
    nobody = _tx(conn, 1700)
    with pytest.raises(ValueError):
        confirm_allocations(conn, out, [("2026-01", -1700)])
    with pytest.raises(ValueError):
        confirm_allocations(conn, nobody, [("2026-01", 1700)])
    with pytest.raises(ValueError):
        confirm_allocations(conn, 9999, [("2026-01", 1700)])
    assert conn.execute("select count(*) from allocation").fetchone()[0] == 0


def test_confirm_replaces_proposed_and_confirmed(conn):
    m = _anna(conn)
    t = _tx(conn, 3400, member_id=m)
    propose_allocations(conn, t)
    confirm_allocations(conn, t, [("2026-02", 1700), ("2026-01", 1700)])
    assert _rows(conn, t) == [
        ("2026-01", 1700, "confirmed", m), ("2026-02", 1700, "confirmed", m)]
    confirm_allocations(conn, t, [("2026-05", 3400)])
    assert _rows(conn, t) == [("2026-05", 3400, "confirmed", m)]
    conn.rollback()
    assert _rows(conn, t) == [("2026-05", 3400, "confirmed", m)]


# --- Prüfungsstart --------------------------------------------------------

def test_check_start_hides_earlier_months(conn):
    m = _anna(conn)
    set_check_start(conn, "2026-06")
    assert get_check_start(conn) == "2026-06"
    today = date(2026, 11, 24)
    assert month_status(conn, m, "2026-05", today) == MonthStatus(
        "2026-05", 0, 0, "NONE")
    assert month_status(conn, m, "2026-06", today).status == "OVERDUE"
    t = _tx(conn, 1700, day="2026-07-01", member_id=m)
    assert propose_allocations(conn, t) == [("2026-06", 1700)]


def test_check_start_before_fee_start_uses_fee_start(conn):
    m = _anna(conn)
    set_check_start(conn, "2025-01")
    t = _tx(conn, 1700, day="2025-12-20", member_id=m)  # Vorauszahlung
    assert propose_allocations(conn, t) == [("2026-01", 1700)]


def test_get_check_start_default(conn):
    assert get_check_start(conn) is None
    _tx(conn, -500, day="2026-02-01")
    _tx(conn, 1700, day="2025-07-03")
    assert get_check_start(conn) == "2025-07"  # Monat der ältesten Buchung
    set_check_start(conn, "2026-03")
    assert get_check_start(conn) == "2026-03"


def test_no_check_start_no_lower_bound(conn):
    m = _anna(conn)
    assert month_status(conn, m, "2026-01", JAN).status == "OPEN"


@pytest.mark.parametrize("bad", ["2026-13", "2026-00", "2026-1", "26-01",
                                 "2026-06-01", "", "abcd-ef"])
def test_set_check_start_validates(conn, bad):
    with pytest.raises(ValueError):
        set_check_start(conn, bad)
    assert get_check_start(conn) is None


def test_stored_check_start_from_settings(conn):
    set_setting(conn, "check_start", "2026-04")
    assert get_check_start(conn) == "2026-04"


# --- Übersicht und Historie -----------------------------------------------

def test_dues_grid(conn):
    b = _anna(conn, "bert")
    a = _anna(conn, "Anna")
    c = _anna(conn, "Carla")
    leave_member(conn, c, date(2026, 3, 5))
    t = _tx(conn, 1700, member_id=a)
    propose_allocations(conn, t)
    grid = dues_grid(conn, 2026, date(2026, 2, 10))
    assert [g["member_id"] for g in grid] == [a, b, c]
    assert [g["name"] for g in grid] == ["Anna", "bert", "Carla"]
    for g in grid:
        assert [s.month for s in g["months"]] == [
            f"2026-{i:02d}" for i in range(1, 13)]
    anna = grid[0]["months"]
    assert anna[0] == MonthStatus("2026-01", 1700, 1700, "PAID")
    assert anna[1] == MonthStatus("2026-02", 1700, 0, "OPEN")
    assert grid[1]["months"][0].status == "OVERDUE"
    carla = grid[2]["months"]
    assert carla[2].status == "OPEN" and carla[3].status == "NONE"


def test_payment_history_mother_pays_for_child(conn):
    kid = _anna(conn, "Kind")
    other = _anna(conn, "Fremd")
    t1 = _tx(conn, 1700, day="2026-01-05", cp_name="Mutter Muster",
             cp_iban=MOM, purpose="Beitrag Kind")
    t2 = _tx(conn, 3400, day="2026-02-05", cp_name="Mutter Muster",
             cp_iban=MOM, purpose="Beitrag Kind Feb Mrz")
    t3 = _tx(conn, 1700, day="2026-02-05", member_id=other)
    assign_member(conn, t1, kid)
    assign_member(conn, t2, kid)
    propose_allocations(conn, t3)
    hist = payment_history(conn, kid)
    assert [h["tx_id"] for h in hist] == [t2, t1]
    assert hist[1] == {
        "tx_id": t1, "booking_date": "2026-01-05", "amount_cents": 1700,
        "cp_name": "Mutter Muster", "purpose": "Beitrag Kind",
        "allocations": [("2026-01", 1700, "confirmed")],
    }
    assert hist[0]["allocations"] == [
        ("2026-02", 1700, "proposed"), ("2026-03", 1700, "proposed")]
    assert payment_history(conn, other)[0]["tx_id"] == t3


def test_payment_history_ties_by_id_desc(conn):
    m = _anna(conn)
    t1 = _tx(conn, 500, day="2026-01-05", member_id=m)
    t2 = _tx(conn, 500, day="2026-01-05", member_id=m)
    hist = payment_history(conn, m)
    assert [h["tx_id"] for h in hist] == [t2, t1]
    assert hist[0]["allocations"] == []


# --- Verteilung aufheben ----------------------------------------------------

def _tx_row(conn, tx_id):
    return conn.execute(
        'select member_id, category_id from "transaction" where id=?', (tx_id,)
    ).fetchone()


def test_clear_undoes_auto_confirm(conn):
    m = _anna(conn)
    t = _tx(conn, 1700, cp_iban=MOM)
    assign_member(conn, t, m)
    assert month_status(conn, m, "2026-01", JAN).status == "PAID"
    before = tuple(_tx_row(conn, t))
    learned = conn.execute("select iban, member_id from member_iban").fetchall()
    assert clear_allocations(conn, t) == 1
    assert month_status(conn, m, "2026-01", JAN) == MonthStatus(
        "2026-01", 1700, 0, "OPEN")
    assert tuple(_tx_row(conn, t)) == before
    assert conn.execute(
        "select iban, member_id from member_iban").fetchall() == learned
    assert _rows(conn, t) == []  # kein neuer Vorschlag
    assert not conn.in_transaction


def test_clear_then_reassign_other_member(conn):
    a, b = _anna(conn, "A"), _anna(conn, "B")
    t = _tx(conn, 1700, cp_iban=MOM)
    assign_member(conn, t, a)
    clear_allocations(conn, t)
    assign_member(conn, t, b)
    assert _tx_row(conn, t)["member_id"] == b
    assert month_status(conn, b, "2026-01", JAN).status == "PAID"
    assert month_status(conn, a, "2026-01", JAN).status == "OPEN"


def test_clear_then_unassign(conn):
    m = _anna(conn)
    t = _tx(conn, 1700, cp_iban=MOM)
    assign_member(conn, t, m)
    clear_allocations(conn, t)
    assign_member(conn, t, None)
    assert _tx_row(conn, t)["member_id"] is None
    assert t in [r["id"] for r in unmatched(conn)]


def test_clear_two_proposed_rows(conn):
    m = _anna(conn)
    t = _tx(conn, 3400, member_id=m)
    propose_allocations(conn, t)
    assert clear_allocations(conn, t) == 2
    assert _rows(conn, t) == []


def test_clear_without_rows_returns_0(conn):
    t = _tx(conn, 1700)
    assert clear_allocations(conn, t) == 0
    assert not conn.in_transaction


def test_clear_unknown_tx_raises(conn):
    m = _anna(conn)
    t = _tx(conn, 1700, member_id=m)
    propose_allocations(conn, t)
    with pytest.raises(ValueError):
        clear_allocations(conn, 9999)
    assert len(_rows(conn, t)) == 1
    assert not conn.in_transaction


def test_clear_leaves_other_tx_untouched(conn):
    m = _anna(conn)
    ta = _tx(conn, 1700, member_id=m)
    tb = _tx(conn, 3400, day="2026-01-06", member_id=m)
    propose_allocations(conn, ta)
    propose_allocations(conn, tb)
    before = _rows(conn, tb)
    assert clear_allocations(conn, ta) == 1
    assert _rows(conn, tb) == before == [
        ("2026-02", 1700, "proposed", m), ("2026-03", 1700, "proposed", m)]


def test_clear_failure_keeps_rows_and_no_open_transaction(conn):
    m = _anna(conn)
    t = _tx(conn, 3400, member_id=m)
    propose_allocations(conn, t)
    # Fehler im Delete erzwingen, robust über einen Trigger
    conn.execute(
        "create trigger no_del before delete on allocation"
        " begin select raise(abort, 'gesperrt'); end")
    conn.commit()
    with pytest.raises(sqlite3.DatabaseError):
        clear_allocations(conn, t)
    assert not conn.in_transaction
    assert len(_rows(conn, t)) == 2


# --- C1: Auto-Bestätigung nur im Buchungsmonat ------------------------------

FAKE = "XX00FAKE0000000001"


def _long_member_13_payments(conn):
    """Mitglied seit 2015, je 17 EUR von Dez 2024 bis Dez 2025 (erfunden)."""
    m = create_member(conn, "Test Person", "Aktiv", date(2015, 3, 1), iban=FAKE)
    ids = [_tx(conn, 1700, day="2024-12-30", cp_iban=FAKE)]
    ids += [_tx(conn, 1700, day=f"2025-{i:02d}-05", cp_iban=FAKE) for i in range(1, 13)]
    return m, ids


def test_long_member_payments_never_confirmed_into_other_months(conn):
    m, ids = _long_member_13_payments(conn)
    assert auto_assign(conn, ids) == 13
    rows = conn.execute(
        'select t.booking_date, a.month, a.status from allocation a'
        ' join "transaction" t on t.id=a.tx_id').fetchall()
    for r in rows:
        if r["status"] == "confirmed":
            assert r["month"] == r["booking_date"][:7]
    # Prüfungsstart ist der Monat der ältesten Buchung: 2024-12
    confirmed = {r["month"] for r in rows if r["status"] == "confirmed"}
    assert confirmed == {"2024-12"} | {f"2025-{i:02d}" for i in range(1, 13)}
    grid = dues_grid(conn, 2025, date(2026, 1, 10))[0]["months"]
    assert {s.status for s in grid} == {"PAID"}
    rep = year_summary(conn, 2024)
    assert rep["by_member"] == [{"name": "Test Person", "paid_cents": 1700}]


def test_arrears_with_explicit_old_check_start_stay_proposed(conn):
    set_check_start(conn, "2024-01")
    m, ids = _long_member_13_payments(conn)
    auto_assign(conn, ids)
    statuses = {r["status"] for r in conn.execute("select status from allocation")}
    assert statuses == {"proposed"}  # Rückstände verteilt der Nutzer
    grid = dues_grid(conn, 2025, date(2026, 1, 10))[0]["months"]
    assert all(s.paid == 0 for s in grid)
    assert year_summary(conn, 2024)["by_member"] == []


def test_payment_in_month_before_due_month_stays_proposed(conn):
    set_check_start(conn, "2026-01")
    m = _anna(conn)
    t = _tx(conn, 1700, day="2025-12-28", member_id=m)
    assert propose_allocations(conn, t) == [("2026-01", 1700)]
    assert _rows(conn, t) == [("2026-01", 1700, "proposed", m)]


def test_payment_in_own_month_auto_confirms(conn):
    set_check_start(conn, "2026-01")
    m = _anna(conn)
    t = _tx(conn, 1700, day="2026-01-28", member_id=m)
    propose_allocations(conn, t)
    assert _rows(conn, t) == [("2026-01", 1700, "confirmed", m)]


def test_check_start_is_set(conn):
    assert check_start_is_set(conn) is False
    _tx(conn, 1700, day="2025-07-03")
    assert check_start_is_set(conn) is False  # nur abgeleitet
    set_check_start(conn, "2025-01")
    assert check_start_is_set(conn) is True


# --- I4: Vorschläge sichtbar machen -----------------------------------------

def test_proposed_months_and_count(conn):
    m = _anna(conn)
    t = _tx(conn, 3400, member_id=m)
    propose_allocations(conn, t)  # 01 + 02 proposed
    t2 = _tx(conn, 1700, day="2027-01-05", member_id=m)
    conn.execute("insert into allocation(tx_id, member_id, month, cents, status)"
                 " values(?,?,?,?, 'confirmed')", (t2, m, "2027-01", 1700))
    assert proposed_months(conn, 2026) == {(m, "2026-01"), (m, "2026-02")}
    assert proposed_months(conn, 2027) == set()
    assert proposed_tx_count(conn) == 1
    confirm_allocations(conn, t, [("2026-01", 1700), ("2026-02", 1700)])
    assert proposed_months(conn, 2026) == set() and proposed_tx_count(conn) == 0


# --- Fix-Runde 2 (N2): Prüfungsstart berechnet offene Vorschläge neu ------------

def _sim3a(conn):
    """Export beginnt Mitte Juli: abgeleiteter Start 2025-07, Zahlungen ab August."""
    m = create_member(conn, "P", "Aktiv", date(2015, 3, 1), iban="XX00FAKE0000000001")
    _tx(conn, -500, day="2025-07-15", cp_iban="XX00FAKEOTHER")
    ids = [_tx(conn, 1700, day=f"2025-{i:02d}-02", cp_iban="XX00FAKE0000000001")
           for i in range(8, 13)]
    assert get_check_start(conn) == "2025-07"
    assert auto_assign(conn, ids) == 5
    return m, ids


def _all_allocs(conn):
    return [tuple(r) for r in conn.execute(
        "select id, tx_id, member_id, month, cents, status from allocation order by id")]


def test_repropose_pending_after_check_start_end_to_end(conn):
    from kassivo.dues import repropose_pending
    m, ids = _sim3a(conn)
    today = date(2026, 1, 10)
    assert all(r[2] == "proposed" for t in ids for r in _rows(conn, t))
    set_check_start(conn, "2025-08")
    assert repropose_pending(conn) == 5
    assert not conn.in_transaction
    for i, t in enumerate(ids, start=8):
        assert _rows(conn, t) == [(f"2025-{i:02d}", 1700, "confirmed", m)]
    months = dues_grid(conn, 2025, today)[0]["months"]
    assert [s.status for s in months[7:]] == ["PAID"] * 5
    assert months[6].status == "NONE"


def test_repropose_pending_leaves_confirmed_untouched_and_counts(conn):
    from kassivo.dues import repropose_pending
    m, ids = _sim3a(conn)
    confirm_allocations(conn, ids[0], [("2025-08", 1700)])
    # eine Buchung mit bestätigter und (künstlich) zusätzlich offener Zeile
    conn.execute("insert into allocation(tx_id, member_id, month, cents, status)"
                 " values(?,?,?,?, 'proposed')", (ids[1], m, "2025-07", 1700))
    confirm_rows = conn.execute(
        "select id from allocation where tx_id=?", (ids[1],)).fetchall()
    conn.execute("update allocation set status='confirmed' where id=?",
                 (confirm_rows[0][0],))
    conn.commit()
    keep = [r for r in _all_allocs(conn) if r[1] in (ids[0], ids[1])]
    set_check_start(conn, "2025-08")
    assert repropose_pending(conn) == 3
    assert [r for r in _all_allocs(conn) if r[1] in (ids[0], ids[1])] == keep


def test_repropose_pending_nothing_open(conn):
    from kassivo.dues import repropose_pending
    assert repropose_pending(conn) == 0
    assert not conn.in_transaction


def test_repropose_pending_atomic_on_error(conn, monkeypatch):
    from kassivo import dues
    m, ids = _sim3a(conn)
    set_check_start(conn, "2025-08")
    before = _all_allocs(conn)
    real, calls = dues.propose_allocations, []

    def flaky(c, tx_id, *, commit=True):
        calls.append(tx_id)
        if len(calls) == 3:
            raise RuntimeError("Testfehler")
        return real(c, tx_id, commit=commit)

    monkeypatch.setattr(dues, "propose_allocations", flaky)
    with pytest.raises(RuntimeError):
        dues.repropose_pending(conn)
    assert calls[:2] == ids[:2]  # chronologisch
    assert _all_allocs(conn) == before
    assert not conn.in_transaction
