"""Mitgliederzuordnung mit Lernen. Nur erfundene Daten."""
from datetime import date

import pytest

from kassivo.categories import create_category
from kassivo.matching import assign_member, auto_assign, unmatched
from kassivo.members import create_member

MOM = "DE22222222222222222222"
KID = "DE33333333333333333333"


def _tx(conn, amount, cp_iban=None, day="2026-01-01", n=[0]):
    n[0] += 1
    conn.execute("insert or ignore into account(id, iban) values(1, 'DE00TEST')")
    cur = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents,'
        " cp_iban, dedupe_key) values(1,?,?,?,?)",
        (day, amount, cp_iban, f"m{n[0]}"),
    )
    return cur.lastrowid


def _member(conn, name="Anna", iban=None):
    return create_member(conn, name, "Aktiv", date(2025, 1, 1), iban=iban)


def _row(conn, tx_id):
    return conn.execute(
        'select member_id, category_id from "transaction" where id=?', (tx_id,)
    ).fetchone()


def _beitraege(conn):
    return conn.execute(
        "select id from category where name='Beiträge'"
    ).fetchone()[0]


def _learned(conn):
    return {
        r["iban"]: r["member_id"]
        for r in conn.execute("select iban, member_id from member_iban")
    }


def _ids(rows):
    return [r["id"] for r in rows]


def test_learn_then_auto_assign(conn):
    m = _member(conn)
    t1, t2 = _tx(conn, 1700, MOM), _tx(conn, 1700, MOM)
    assign_member(conn, t1, m, learn=True)
    assert auto_assign(conn, [t2]) == 1
    assert _row(conn, t2)["member_id"] == m
    assert _row(conn, t2)["category_id"] == _beitraege(conn)
    assert t2 not in _ids(unmatched(conn))


def test_learn_false_learns_nothing(conn):
    m = _member(conn)
    t1, t2 = _tx(conn, 1700, MOM), _tx(conn, 1700, MOM)
    assign_member(conn, t1, m, learn=False)
    assert _learned(conn) == {}
    assert auto_assign(conn, [t2]) == 0
    assert _row(conn, t2)["member_id"] is None


def test_outgoing_never_auto_assigned(conn):
    m = _member(conn, iban=KID)
    t = _tx(conn, -1700, KID)
    assert auto_assign(conn, [t]) == 0
    assert _row(conn, t)["member_id"] is None
    assert t not in _ids(unmatched(conn))


def test_zero_amount_not_incoming(conn):
    _member(conn, iban=KID)
    t = _tx(conn, 0, KID)
    assert auto_assign(conn, [t]) == 0
    assert t not in _ids(unmatched(conn))


def test_no_cp_iban_stays_unmatched(conn):
    _member(conn, iban=KID)
    t = _tx(conn, 1700, None)
    assert auto_assign(conn, [t]) == 0
    assert t in _ids(unmatched(conn))


def test_iban_spaces_and_case_ignored(conn):
    m = _member(conn, iban="de33 3333 3333 3333 3333 33")
    t = _tx(conn, 1700, "DE33 3333 3333 3333 3333 33")
    assert auto_assign(conn, [t]) == 1
    assert _row(conn, t)["member_id"] == m


def test_learned_iban_normalized(conn):
    m = _member(conn)
    t1 = _tx(conn, 1700, " de22 2222 2222 2222 2222 22 ")
    t2 = _tx(conn, 1700, MOM)
    assign_member(conn, t1, m)
    assert _learned(conn) == {MOM: m}
    assert auto_assign(conn, [t2]) == 1


def test_mother_pays_for_child(conn):
    kid = _member(conn, "Kind", iban=KID)
    t1, t2 = _tx(conn, 1700, MOM), _tx(conn, 1700, MOM)
    assert auto_assign(conn, [t1]) == 0
    assign_member(conn, t1, kid)
    assert auto_assign(conn, [t2]) == 1
    assert _row(conn, t2)["member_id"] == kid


def test_member_iban_before_member_iban_column(conn):
    a = _member(conn, "A", iban=MOM)
    b = _member(conn, "B")
    t1, t2 = _tx(conn, 1700, MOM), _tx(conn, 1700, MOM)
    assign_member(conn, t1, b)
    assert auto_assign(conn, [t2]) == 1
    assert _row(conn, t2)["member_id"] == b
    assert _row(conn, t2)["member_id"] != a
    assert _learned(conn) == {MOM: b}


def test_ambiguous_iban_skipped(conn):
    _member(conn, "A", iban=KID)
    _member(conn, "B", iban="de33333333333333333333")
    t = _tx(conn, 1700, KID)
    assert auto_assign(conn, [t]) == 0
    assert _row(conn, t)["member_id"] is None


def test_latest_manual_assignment_wins(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    t1, t2, t3 = _tx(conn, 1700, MOM), _tx(conn, 1700, MOM), _tx(conn, 1700, MOM)
    assign_member(conn, t1, a)
    assign_member(conn, t2, b)
    assert _learned(conn) == {MOM: b}
    assert auto_assign(conn, [t3]) == 1
    assert _row(conn, t3)["member_id"] == b


def test_auto_assign_writes_nothing_to_member_iban(conn):
    _member(conn, iban=KID)
    t = _tx(conn, 1700, KID)
    assert auto_assign(conn, [t]) == 1
    assert _learned(conn) == {}


def test_auto_assign_only_given_ids_and_unassigned(conn):
    a, b = _member(conn, "A", iban=KID), _member(conn, "B")
    t1, t2, t3 = _tx(conn, 1700, KID), _tx(conn, 1700, KID), _tx(conn, 1700, KID)
    assign_member(conn, t2, b, learn=False)
    assert auto_assign(conn, [t2, t3]) == 1
    assert _row(conn, t1)["member_id"] is None
    assert _row(conn, t2)["member_id"] == b
    assert _row(conn, t3)["member_id"] == a


def test_auto_assign_empty_list(conn):
    assert auto_assign(conn, []) == 0


def test_existing_category_never_overwritten(conn):
    other = create_category(conn, "Spende", "in")
    m = _member(conn, iban=KID)
    t1, t2 = _tx(conn, 1700, MOM), _tx(conn, 1700, KID)
    conn.execute('update "transaction" set category_id=? where id in (?,?)',
                 (other, t1, t2))
    assign_member(conn, t1, m)
    auto_assign(conn, [t2])
    assert _row(conn, t1)["category_id"] == other
    assert _row(conn, t2)["category_id"] == other


def test_assign_sets_beitraege_when_empty(conn):
    m = _member(conn)
    t = _tx(conn, 1700, MOM)
    assign_member(conn, t, m)
    assert _row(conn, t)["category_id"] == _beitraege(conn)


def test_assign_without_cp_iban(conn):
    m = _member(conn)
    t = _tx(conn, 1700, None)
    assign_member(conn, t, m)
    assert _row(conn, t)["member_id"] == m
    assert _learned(conn) == {}


def test_outgoing_assign_rejected(conn):
    m = _member(conn)
    t = _tx(conn, -1700, MOM)
    with pytest.raises(ValueError):
        assign_member(conn, t, m)
    assert _row(conn, t)["member_id"] is None
    assert _learned(conn) == {}


def test_zero_assign_rejected(conn):
    m = _member(conn)
    t = _tx(conn, 0, MOM)
    with pytest.raises(ValueError):
        assign_member(conn, t, m)


def test_unknown_tx_or_member_rejected(conn):
    m = _member(conn)
    t = _tx(conn, 1700, MOM)
    with pytest.raises(ValueError):
        assign_member(conn, 9999, m)
    with pytest.raises(ValueError):
        assign_member(conn, t, 9999)
    assert _row(conn, t)["member_id"] is None
    assert _learned(conn) == {}


def test_error_message_has_no_full_iban(conn):
    m = _member(conn)
    t = _tx(conn, -1700, MOM)
    with pytest.raises(ValueError) as e:
        assign_member(conn, t, m)
    assert MOM not in str(e.value)


def _alloc(conn, tx_id, member_id, status):
    conn.execute(
        "insert into allocation(tx_id, member_id, month, cents, status)"
        " values(?,?,?,?,?)",
        (tx_id, member_id, "2026-01", 1700, status),
    )


def _allocs(conn, tx_id):
    return [r["status"] for r in conn.execute(
        "select status from allocation where tx_id=? order by id", (tx_id,))]


def test_unassign_keeps_category_and_learned_iban(conn):
    m = _member(conn)
    t = _tx(conn, 1700, MOM)
    assign_member(conn, t, m)
    assign_member(conn, t, None)
    assert _row(conn, t)["member_id"] is None
    assert _row(conn, t)["category_id"] == _beitraege(conn)
    assert _learned(conn) == {MOM: m}
    assert t in _ids(unmatched(conn))


def test_unassign_deletes_proposed_allocations(conn):
    m = _member(conn)
    t = _tx(conn, 1700, MOM)
    assign_member(conn, t, m)
    _alloc(conn, t, m, "proposed")
    _alloc(conn, t, m, "proposed")
    assign_member(conn, t, None)
    assert _allocs(conn, t) == []


def test_confirmed_allocation_blocks_unassign(conn):
    m = _member(conn)
    t = _tx(conn, 1700, MOM)
    assign_member(conn, t, m)
    _alloc(conn, t, m, "proposed")
    _alloc(conn, t, m, "confirmed")
    with pytest.raises(ValueError, match="Verteilung zuerst aufheben"):
        assign_member(conn, t, None)
    assert _row(conn, t)["member_id"] == m
    assert _allocs(conn, t) == ["proposed", "confirmed"]


def test_confirmed_allocation_blocks_reassign_other(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    t = _tx(conn, 1700, MOM)
    assign_member(conn, t, a)
    _alloc(conn, t, a, "confirmed")
    with pytest.raises(ValueError, match="Verteilung zuerst aufheben"):
        assign_member(conn, t, b)
    assert _row(conn, t)["member_id"] == a
    assert _learned(conn) == {MOM: a}


def test_reassign_same_member_keeps_allocations(conn):
    m = _member(conn)
    t = _tx(conn, 1700, MOM)
    assign_member(conn, t, m)
    _alloc(conn, t, m, "proposed")
    _alloc(conn, t, m, "confirmed")
    assign_member(conn, t, m)
    assert _row(conn, t)["member_id"] == m
    assert _allocs(conn, t) == ["proposed", "confirmed"]


def test_reassign_other_member_deletes_proposed(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    t = _tx(conn, 1700, MOM)
    assign_member(conn, t, a)
    _alloc(conn, t, a, "proposed")
    assign_member(conn, t, b)
    assert _row(conn, t)["member_id"] == b
    assert _allocs(conn, t) == []


def test_reassign_other_member_confirmed_still_raises(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    t = _tx(conn, 1700, MOM)
    assign_member(conn, t, a)
    _alloc(conn, t, a, "proposed")
    _alloc(conn, t, a, "confirmed")
    with pytest.raises(ValueError, match="Verteilung zuerst aufheben"):
        assign_member(conn, t, b)
    assert _allocs(conn, t) == ["proposed", "confirmed"]


def test_assign_from_none_deletes_stale_proposed(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    t = _tx(conn, 1700, MOM)
    _alloc(conn, t, a, "proposed")
    assign_member(conn, t, b)
    assert _allocs(conn, t) == []


def _leave(conn, member_id, day):
    conn.execute("update member set austritt=? where id=?", (day, member_id))


def test_left_member_learned_iban_not_auto_assigned(conn):
    a = _member(conn, "A")
    t1 = _tx(conn, 1700, MOM, day="2026-01-01")
    t2 = _tx(conn, 1700, MOM, day="2026-03-01")
    assign_member(conn, t1, a)
    _leave(conn, a, "2026-02-28")
    assert auto_assign(conn, [t2]) == 0
    assert _row(conn, t2)["member_id"] is None
    assert t2 in _ids(unmatched(conn))


def test_left_member_column_iban_not_auto_assigned(conn):
    a = _member(conn, "A", iban=KID)
    _leave(conn, a, "2026-02-28")
    t = _tx(conn, 1700, KID, day="2026-03-01")
    assert auto_assign(conn, [t]) == 0
    assert t in _ids(unmatched(conn))


def test_booking_at_or_before_austritt_still_assigned(conn):
    a = _member(conn, "A", iban=KID)
    _leave(conn, a, "2026-02-28")
    t1 = _tx(conn, 1700, KID, day="2026-02-28")
    t2 = _tx(conn, 1700, KID, day="2026-02-01")
    assert auto_assign(conn, [t1, t2]) == 2
    assert _row(conn, t1)["member_id"] == a
    assert _row(conn, t2)["member_id"] == a


def test_left_learned_member_no_fall_through_to_member_iban(conn):
    a = _member(conn, "A")
    b = _member(conn, "B", iban=MOM)
    t1 = _tx(conn, 1700, MOM, day="2026-01-01")
    t2 = _tx(conn, 1700, MOM, day="2026-03-01")
    assign_member(conn, t1, a)
    _leave(conn, a, "2026-02-28")
    assert auto_assign(conn, [t2]) == 0
    assert _row(conn, t2)["member_id"] is None
    t3 = _tx(conn, 1700, KID, day="2026-03-01")
    conn.execute("update member set iban=? where id=?", (KID, b))
    assert auto_assign(conn, [t3]) == 1  # b selbst ist aktiv und zuordenbar


def test_manual_assign_to_left_member_allowed(conn):
    a = _member(conn, "A")
    _leave(conn, a, "2026-02-28")
    t = _tx(conn, 1700, MOM, day="2026-03-01")
    assign_member(conn, t, a)
    assert _row(conn, t)["member_id"] == a


def test_unmatched_order_newest_first(conn):
    t1 = _tx(conn, 100, day="2026-01-05")
    t2 = _tx(conn, 100, day="2026-02-01")
    t3 = _tx(conn, 100, day="2026-01-05")
    _tx(conn, -100, day="2026-03-01")
    assert _ids(unmatched(conn)) == [t2, t3, t1]


def test_assign_persists_after_commit(conn):
    m = _member(conn)
    t = _tx(conn, 1700, MOM)
    assign_member(conn, t, m)
    conn.rollback()
    assert _row(conn, t)["member_id"] == m
