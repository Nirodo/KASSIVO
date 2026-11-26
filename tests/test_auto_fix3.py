"""Fix-Runde 3 zu #27: Beitrag, Art, Austritt und Beitragsbeginn verteilen neu. Erfundene Daten."""
import random
import sqlite3
from datetime import date

import pytest

from kassivo import dues
from kassivo.db import connect
from kassivo.dues import (
    auto_distribute_pending, confirm_allocations, propose_allocations, redistribute_all,
    review_reason, set_check_start,
)
from kassivo.fees import set_fee
from kassivo.matching import assign_member
from kassivo.members import (
    create_member, expected_cents, leave_member, set_member_type, update_member,
)


def _tx(conn, amount, day, member_id=None, n=[0]):
    n[0] += 1
    conn.execute("insert or ignore into account(id, iban) values(1, 'XX00FAKEKONTO')")
    cur = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, member_id,'
        " dedupe_key) values(1,?,?,?,?)", (day, amount, member_id, f"f3x{n[0]}"))
    conn.commit()
    return cur.lastrowid


def _rows(conn, tx):
    return [tuple(r) for r in conn.execute(
        "select month, cents, status, source from allocation where tx_id=? order by month, id",
        (tx,))]


def _state(conn):
    return sorted(tuple(r) for r in conn.execute(
        "select tx_id, member_id, month, cents, status, source from allocation"))


def _canonical(conn):
    copy = sqlite3.connect(":memory:")
    conn.backup(copy)
    copy.row_factory = sqlite3.Row
    try:
        redistribute_all(copy)
        return _state(copy)
    finally:
        copy.close()


def _no_overallocation(conn):
    for r in conn.execute("select member_id, month, sum(cents) as s from allocation"
                          " where status='confirmed' and source='auto'"
                          " group by member_id, month").fetchall():
        assert r["s"] <= expected_cents(conn, r["member_id"], r["month"]), tuple(r)


@pytest.fixture
def anna(conn):
    set_check_start(conn, "2026-01")
    return create_member(conn, "Anna", "Aktiv", date(2025, 12, 10))


def _paid_jan_feb_and_march(conn, anna):
    for day in ("2026-01-05", "2026-02-05"):
        propose_allocations(conn, _tx(conn, 1700, day, anna))
    t = _tx(conn, 3400, "2026-03-03", anna)
    propose_allocations(conn, t)
    assert _rows(conn, t) == [("2026-03", 1700, "confirmed", "auto"),
                              ("2026-04", 1700, "confirmed", "auto")]
    return t


# --- Repros ------------------------------------------------------------------------------

def test_austritt_makes_april_payment_a_proposal(conn, anna):
    t = _paid_jan_feb_and_march(conn, anna)
    leave_member(conn, anna, date(2026, 3, 20))
    assert not conn.in_transaction
    assert _rows(conn, t) == [("2026-03", 3400, "proposed", "auto")]
    assert review_reason(conn, t) == "Rest ohne offenen Monat"
    assert _state(conn) == _canonical(conn)


def test_type_change_recomputes(conn, anna):
    ids = [_tx(conn, 1700, f"2026-{i:02d}-05", anna) for i in (1, 2, 3)]
    for t in ids:
        propose_allocations(conn, t)
    set_member_type(conn, anna, "Passiv", "2026-02")
    assert not conn.in_transaction
    _no_overallocation(conn)
    assert _state(conn) == _canonical(conn)
    assert _rows(conn, ids[0]) == [("2026-01", 1700, "confirmed", "auto")]
    assert {r[2] for r in _rows(conn, ids[1])} == {"proposed"}


def test_fee_change_recomputes_all(conn, anna):
    bert = create_member(conn, "Bert", "Aktiv", date(2025, 12, 10))
    a = _tx(conn, 3400, "2026-01-05", anna)
    b = _tx(conn, 1700, "2026-01-05", bert)
    for t in (a, b):
        propose_allocations(conn, t)
    set_fee(conn, "Aktiv", 2000, "2026-01")
    assert not conn.in_transaction
    _no_overallocation(conn)
    assert _state(conn) == _canonical(conn)
    assert _rows(conn, b) == [("2026-01", 1700, "confirmed", "auto")]  # Teilzahlung
    assert {r[2] for r in _rows(conn, a)} == {"proposed"}  # 2000 + 1400


def test_fee_start_change_recomputes(conn, anna):
    t = _tx(conn, 1700, "2026-01-05", anna)
    propose_allocations(conn, t)
    update_member(conn, anna, fee_start="2026-02")
    assert _rows(conn, t) == [("2026-02", 1700, "confirmed", "auto")]
    assert _state(conn) == _canonical(conn)


def test_manual_rows_survive_fee_change(conn, anna):
    t = _tx(conn, 1700, "2026-01-05", anna)
    confirm_allocations(conn, t, [("2026-01", 1700)])
    set_fee(conn, "Aktiv", 2000, "2026-01")
    leave_member(conn, anna, date(2026, 1, 20))
    assert _rows(conn, t) == [("2026-01", 1700, "confirmed", "manual")]


@pytest.mark.parametrize("change", [
    lambda c, m: leave_member(c, m, date(2026, 3, 20)),
    lambda c, m: set_member_type(c, m, "Passiv", "2026-02"),
    lambda c, m: update_member(c, m, fee_start="2026-02"),
    lambda c, m: set_fee(c, "Aktiv", 2000, "2026-01"),
])
def test_change_is_atomic(conn, anna, monkeypatch, change):
    _paid_jan_feb_and_march(conn, anna)
    before = _state(conn)
    member_before = tuple(conn.execute("select * from member where id=?", (anna,)).fetchone())
    fees_before = conn.execute("select * from fee_rate order by type_id, valid_from").fetchall()
    types_before = conn.execute("select * from member_type_history").fetchall()

    def boom(*a, **k):
        raise RuntimeError("Testfehler")

    monkeypatch.setattr(dues, "_passes", boom)
    with pytest.raises(RuntimeError):
        change(conn, anna)
    assert not conn.in_transaction
    assert _state(conn) == before
    assert tuple(conn.execute("select * from member where id=?", (anna,)).fetchone()) == \
        member_before
    assert [tuple(r) for r in conn.execute(
        "select * from fee_rate order by type_id, valid_from")] == [tuple(r) for r in fees_before]
    assert [tuple(r) for r in conn.execute("select * from member_type_history")] == \
        [tuple(r) for r in types_before]


def test_set_check_start_and_redistribute_atomic(conn, anna, monkeypatch):
    _paid_jan_feb_and_march(conn, anna)
    before = _state(conn)
    monkeypatch.setattr(dues, "_passes", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        dues.set_check_start_redistribute(conn, "2026-02")
    assert not conn.in_transaction
    assert dues.get_check_start(conn) == "2026-01" and _state(conn) == before


def test_set_check_start_redistribute_counts(conn, anna):
    _paid_jan_feb_and_march(conn, anna)
    assert dues.set_check_start_redistribute(conn, "2026-02") == (0, 1)  # Januar entfällt
    assert dues.get_check_start(conn) == "2026-02"
    with pytest.raises(ValueError):
        dues.set_check_start_redistribute(conn, "2026-13")


def test_count_changed_helper(conn, anna):
    t = _paid_jan_feb_and_march(conn, anna)
    before = dues.auto_snapshot(conn, anna)
    leave_member(conn, anna, date(2026, 3, 20))
    after = dues.auto_snapshot(conn, anna)
    assert dues.count_changed(before, after) == 1
    assert dues.count_changed(before, after, exclude=t) == 0
    assert dues.count_changed(after, after) == 0


# --- Fuzz Modus B: Stammdaten ändern ---------------------------------------------------------

@pytest.mark.parametrize("seed", range(40))
def test_fuzz_master_data_changes_stay_canonical(seed):
    rnd = random.Random(3000 + seed)
    conn = connect(":memory:")
    try:
        set_check_start(conn, "2026-01")
        members = [create_member(conn, f"M{i}", rnd.choice(["Aktiv", "Passiv"]),
                                 date(2025, 12, 5)) for i in range(rnd.randint(1, 3))]
        for _ in range(25):
            op = rnd.choice(["pay", "pay", "fee", "type", "leave", "fee_start", "confirm"])
            m = rnd.choice(members)
            manual = sorted(tuple(r) for r in conn.execute(
                "select * from allocation where source='manual'"))
            if op == "pay":
                t = _tx(conn, rnd.choice([1700, 3400, 1000, 700, 2000, 20400]),
                        f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}")
                assign_member(conn, t, m, learn=False)
            elif op == "fee":
                set_fee(conn, rnd.choice(["Aktiv", "Passiv"]), rnd.choice([1000, 1700, 2000]),
                        f"2026-{rnd.randint(1, 12):02d}")
            elif op == "type":
                set_member_type(conn, m, rnd.choice(["Aktiv", "Passiv"]),
                                f"2026-{rnd.randint(1, 12):02d}")
            elif op == "leave":
                leave_member(conn, m, date(2026, rnd.randint(1, 12), 15))
            elif op == "fee_start":
                try:
                    update_member(conn, m, fee_start=f"2026-{rnd.randint(1, 4):02d}")
                except sqlite3.IntegrityError:
                    pass  # Art-Historie hat den Monat schon (vorhandene Grenze), nichts geändert
            else:
                props = [r[0] for r in conn.execute(
                    "select distinct tx_id from allocation where status='proposed'")]
                if props:
                    t = rnd.choice(props)
                    confirm_allocations(conn, t, [(r[0], r[1]) for r in _rows(conn, t)])
            if op != "confirm":
                assert sorted(tuple(r) for r in conn.execute(
                    "select * from allocation where source='manual'")) == manual, op
            assert not conn.in_transaction
            _no_overallocation(conn)
            assert _state(conn) == _canonical(conn), op
            snap = _state(conn)
            assert auto_distribute_pending(conn).confirmed == 0 and _state(conn) == snap
    finally:
        conn.close()


# --- Zählung über 0 (Altbestand) -------------------------------------------------------------

def test_repropose_count_above_zero(conn, anna):
    ids = [_tx(conn, 1700, f"2026-{i:02d}-05", anna) for i in (1, 2, 3)]
    for t in ids:
        propose_allocations(conn, t)
    conn.execute("update allocation set status='proposed'")  # Altbestand vor #27
    conn.commit()
    assert dues.repropose_pending(conn) == 3
    assert all(_rows(conn, t)[0][2] == "confirmed" for t in ids)
