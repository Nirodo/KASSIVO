"""Prognose offener Beiträge. Nur erfundene Daten."""
from datetime import date, datetime

import pytest

from kassivo.dues import set_check_start
from kassivo.fees import set_fee
from kassivo.forecast import Forecast, forecast
from kassivo.members import create_member, leave_member

TODAY = date(2026, 11, 24)
NOV_END = date(2026, 11, 30)


def _tx(conn, amount, member_id, day="2026-11-05", n=[0]):
    n[0] += 1
    conn.execute("insert or ignore into account(id, iban) values(1, 'DE00TEST')")
    cur = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents,'
        " member_id, dedupe_key) values(1,?,?,?,?)",
        (day, amount, member_id, f"f{n[0]}"),
    )
    conn.commit()
    return cur.lastrowid


def _alloc(conn, member_id, month, cents, status="confirmed"):
    tx = _tx(conn, cents, member_id)
    conn.execute(
        "insert into allocation(tx_id, member_id, month, cents, status)"
        " values(?,?,?,?,?)", (tx, member_id, month, cents, status),
    )
    conn.commit()


def _two(conn, eintritt=date(2026, 10, 10)):
    # Eintritt Oktober -> Beitrag ab 2026-11
    a = create_member(conn, "Anna Aktiv", "Aktiv", eintritt)
    p = create_member(conn, "Paul Passiv", "Passiv", eintritt)
    return a, p


def test_nobody_paid_current_month(conn):
    a, p = _two(conn)
    f = forecast(conn, NOV_END, TODAY)
    assert f == Forecast(0, 2700, [(a, "2026-11", 1700), (p, "2026-11", 1000)])


def test_one_paid_reduces_due(conn):
    a, p = _two(conn)
    _alloc(conn, a, "2026-11", 1700)
    f = forecast(conn, NOV_END, TODAY)
    assert f.due_cents == 1000
    assert f.overdue_cents == 0
    assert f.items == [(p, "2026-11", 1000)]


def test_unpaid_past_month_is_overdue(conn):
    a, p = _two(conn, eintritt=date(2026, 9, 10))  # Beitrag ab 2026-10
    _alloc(conn, p, "2026-10", 1000)
    f = forecast(conn, NOV_END, TODAY)
    assert f.overdue_cents == 1700
    assert f.due_cents == 2700
    assert f.items == [
        (a, "2026-10", 1700), (a, "2026-11", 1700), (p, "2026-11", 1000)]


def test_until_two_months_later_triples(conn):
    _two(conn)
    assert forecast(conn, date(2026, 12, 1), TODAY).due_cents == 5400
    assert forecast(conn, date(2027, 1, 31), TODAY).due_cents == 8100


def test_left_member_counts_only_through_austritt_month(conn):
    a, p = _two(conn, eintritt=date(2026, 9, 10))  # Beitrag ab 2026-10
    leave_member(conn, p, date(2026, 10, 15))
    f = forecast(conn, date(2027, 1, 31), TODAY)
    assert f.overdue_cents == 1700 + 1000  # Austrittsmonat 2026-10 zählt voll
    assert f.due_cents == 3 * 1700
    assert all(m <= "2026-10" for mid, m, _ in f.items if mid == p)


def test_partial_current_month(conn):
    a, p = _two(conn)
    _alloc(conn, a, "2026-11", 1000)
    f = forecast(conn, NOV_END, TODAY)
    assert f.due_cents == 700 + 1000
    assert (a, "2026-11", 700) in f.items


def test_partial_past_month_is_overdue_remainder(conn):
    a, p = _two(conn, eintritt=date(2026, 9, 10))
    _alloc(conn, a, "2026-10", 1000)
    _alloc(conn, p, "2026-10", 1000)
    f = forecast(conn, date(2026, 10, 31), TODAY)
    assert f == Forecast(700, 0, [(a, "2026-10", 700)])


def test_proposed_allocation_does_not_reduce(conn):
    a, p = _two(conn)
    _alloc(conn, a, "2026-11", 1700, status="proposed")
    assert forecast(conn, NOV_END, TODAY).due_cents == 2700


def test_overpayment_never_negative(conn):
    a, p = _two(conn)
    _alloc(conn, a, "2026-11", 5000)
    f = forecast(conn, NOV_END, TODAY)
    assert f == Forecast(0, 1000, [(p, "2026-11", 1000)])


def test_no_members_empty(conn):
    assert forecast(conn, NOV_END, TODAY) == Forecast(0, 0, [])


def test_until_before_start_empty(conn):
    _two(conn)  # Beitrag ab 2026-11
    assert forecast(conn, date(2026, 10, 31), TODAY) == Forecast(0, 0, [])


def test_until_before_check_start_empty(conn):
    _two(conn, eintritt=date(2026, 9, 10))
    set_check_start(conn, "2026-11")
    assert forecast(conn, date(2026, 10, 31), TODAY) == Forecast(0, 0, [])


def test_check_start_hides_earlier_overdue(conn):
    a, p = _two(conn, eintritt=date(2026, 8, 10))  # Beitrag ab 2026-09
    set_check_start(conn, "2026-11")
    f = forecast(conn, NOV_END, TODAY)
    assert f == Forecast(0, 2700, [(a, "2026-11", 1700), (p, "2026-11", 1000)])


def test_until_in_past_covers_fewer_months(conn):
    a, p = _two(conn, eintritt=date(2026, 8, 10))  # Beitrag ab 2026-09
    f = forecast(conn, date(2026, 9, 15), TODAY)
    assert f == Forecast(2700, 0, [(a, "2026-09", 1700), (p, "2026-09", 1000)])


@pytest.mark.parametrize("bad", [
    datetime(2026, 11, 30), "2026-11-30", None, 20261130])
def test_invalid_until_raises(conn, bad):
    with pytest.raises(ValueError):
        forecast(conn, bad, TODAY)


@pytest.mark.parametrize("bad", [
    datetime(2026, 11, 24), "2026-11-24", None])
def test_invalid_today_raises(conn, bad):
    with pytest.raises(ValueError):
        forecast(conn, NOV_END, bad)


def test_items_sorted_by_month_then_member(conn):
    # Zora zuerst anlegen: kleinere id, aber Name sortiert später
    p = create_member(conn, "Zora", "Passiv", date(2026, 9, 10))
    a = create_member(conn, "Adam", "Aktiv", date(2026, 10, 10))
    f = forecast(conn, date(2026, 12, 31), TODAY)
    assert f.items == [
        (p, "2026-10", 1000),
        (p, "2026-11", 1000), (a, "2026-11", 1700),
        (p, "2026-12", 1000), (a, "2026-12", 1700),
    ]
    assert f.overdue_cents == 1000
    assert f.due_cents == 2 * 2700


def test_fee_change_used_for_future_months(conn):
    a, p = _two(conn)
    set_fee(conn, "Aktiv", 2000, "2026-12")
    f = forecast(conn, date(2026, 12, 31), TODAY)
    assert f.due_cents == 1700 + 1000 + 2000 + 1000
    assert (a, "2026-12", 2000) in f.items


def test_forecast_is_frozen(conn):
    f = forecast(conn, NOV_END, TODAY)
    with pytest.raises(Exception):
        f.due_cents = 1


def test_until_year_9999_raises_fast(conn):
    import time
    _two(conn)
    t = time.monotonic()
    with pytest.raises(ValueError):
        forecast(conn, date(9999, 12, 31), TODAY)
    assert time.monotonic() - t < 1


def test_horizon_boundary(conn):
    _two(conn)
    # TODAY-Monat 2026-11 + 600 Monate = 2076-11
    assert isinstance(forecast(conn, date(2076, 11, 30), TODAY), Forecast)
    with pytest.raises(ValueError):
        forecast(conn, date(2076, 12, 1), TODAY)
