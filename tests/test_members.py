from datetime import date

from kassivo.fees import set_fee
from kassivo.members import (
    create_member, expected_cents, leave_member, list_members,
    set_member_type, type_at, update_member,
)


def test_fee_starts_month_after_eintritt(conn):
    m = create_member(conn, "Erika Muster", "Aktiv", date(2026, 3, 15))
    assert expected_cents(conn, m, "2026-03") == 0
    assert expected_cents(conn, m, "2026-04") == 1700
    assert type_at(conn, m, "2026-02") is None
    assert type_at(conn, m, "2026-04") == "Aktiv"


def test_explicit_fee_start(conn):
    m = create_member(conn, "Max", "Aktiv", date(2026, 3, 15), fee_start="2026-01")
    assert expected_cents(conn, m, "2026-01") == 1700


def test_type_change(conn):
    m = create_member(conn, "Max", "Aktiv", date(2026, 1, 1))
    set_member_type(conn, m, "Passiv", "2026-09")
    assert expected_cents(conn, m, "2026-08") == 1700
    assert expected_cents(conn, m, "2026-09") == 1000


def test_austritt(conn):
    m = create_member(conn, "Max", "Aktiv", date(2026, 1, 1))
    leave_member(conn, m, date(2026, 10, 12))
    assert expected_cents(conn, m, "2026-10") == 1700
    assert expected_cents(conn, m, "2026-11") == 0


def test_fee_change_keeps_past(conn):
    m = create_member(conn, "Max", "Aktiv", date(2026, 1, 1))
    set_fee(conn, "Aktiv", 2000, "2027-01")
    assert expected_cents(conn, m, "2026-12") == 1700
    assert expected_cents(conn, m, "2027-01") == 2000


def test_update_and_list(conn):
    a = create_member(conn, "A", "Aktiv", date(2026, 1, 1))
    b = create_member(conn, "B", "Passiv", date(2026, 1, 1))
    update_member(conn, a, city="Köln", iban="DE00")
    leave_member(conn, b, date(2026, 5, 1))
    assert [r["name"] for r in list_members(conn)] == ["A", "B"]
    assert [r["name"] for r in list_members(conn, active_only=True)] == ["A"]
    assert conn.execute("select city from member where id=?", (a,)).fetchone()[0] == "Köln"
