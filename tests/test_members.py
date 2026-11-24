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


def test_fee_start_earlier_moves_first_history(conn):
    m = create_member(conn, "Max", "Aktiv", date(2026, 3, 1), fee_start="2026-04")
    update_member(conn, m, fee_start="2026-01")
    assert type_at(conn, m, "2026-01") == "Aktiv"
    assert expected_cents(conn, m, "2026-01") == 1700


def test_fee_start_earlier_keeps_later_type_change(conn):
    m = create_member(conn, "Max", "Aktiv", date(2026, 3, 1), fee_start="2026-04")
    set_member_type(conn, m, "Passiv", "2026-09")
    update_member(conn, m, fee_start="2026-01")
    assert expected_cents(conn, m, "2026-01") == 1700
    assert expected_cents(conn, m, "2026-08") == 1700
    assert expected_cents(conn, m, "2026-09") == 1000
    assert expected_cents(conn, m, "2026-12") == 1000


def test_fee_start_later(conn):
    m = create_member(conn, "Max", "Aktiv", date(2026, 3, 1), fee_start="2026-04")
    update_member(conn, m, fee_start="2026-06")
    assert expected_cents(conn, m, "2026-05") == 0
    assert expected_cents(conn, m, "2026-06") == 1700
    rows = conn.execute(
        "select valid_from from member_type_history where member_id=?", (m,)
    ).fetchall()
    assert [r[0] for r in rows] == ["2026-06"]


def test_update_converts_dates(conn):
    m = create_member(conn, "Max", "Aktiv", date(2026, 3, 1))
    update_member(conn, m, austritt=date(2026, 10, 12))
    assert conn.execute("select austritt from member where id=?", (m,)).fetchone()[0] == "2026-10-12"


def test_iban_normalized_on_store(conn):
    m = create_member(conn, "Max", "Aktiv", date(2026, 1, 1), iban=" de00 1111 2222 ")
    assert conn.execute("select iban from member where id=?", (m,)).fetchone()[0] == "DE0011112222"
    update_member(conn, m, iban="de99 3333")
    assert conn.execute("select iban from member where id=?", (m,)).fetchone()[0] == "DE993333"
    update_member(conn, m, iban="  ")
    assert conn.execute("select iban from member where id=?", (m,)).fetchone()[0] is None
    m2 = create_member(conn, "Eva", "Aktiv", date(2026, 1, 1), iban="")
    assert conn.execute("select iban from member where id=?", (m2,)).fetchone()[0] is None
