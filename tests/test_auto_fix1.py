"""Fix-Runde 1 zu #27: abgeleitete (auto) und manuelle Verteilungen. Nur erfundene Daten."""
import random
from datetime import date

import pytest

from kassivo import dues
from kassivo.db import connect
from kassivo.dues import (
    AutoResult, auto_distribute_pending, clear_allocations, confirm_allocations, month_status,
    propose_allocations, redistribute_all, review_reason, set_check_start, undo_auto,
)
from kassivo.fees import set_fee
from kassivo.matching import assign_member, auto_assign
from kassivo.members import create_member, expected_cents, leave_member

FAKE = "XX00FAKE0000000001"


def _tx(conn, amount, day, member_id=None, cp_iban=None, n=[0]):
    n[0] += 1
    conn.execute("insert or ignore into account(id, iban) values(1, 'XX00FAKEKONTO')")
    cur = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, cp_iban,'
        " member_id, dedupe_key) values(1,?,?,?,?,?)",
        (day, amount, cp_iban, member_id, f"fx{n[0]}"))
    conn.commit()
    return cur.lastrowid


def _m(conn, name="Anna", type_name="Aktiv"):
    return create_member(conn, name, type_name, date(2025, 12, 10))  # zahlt ab 2026-01


def _rows(conn, tx):
    return [tuple(r) for r in conn.execute(
        "select month, cents, status, source from allocation where tx_id=? order by month, id",
        (tx,))]


def _state(conn):
    return sorted(tuple(r) for r in conn.execute(
        "select tx_id, member_id, month, cents, status, source from allocation"))


@pytest.fixture
def anna(conn):
    set_check_start(conn, "2026-01")
    return _m(conn)


# --- I1: Fixpunkt ------------------------------------------------------------------

def test_i1_repro_second_run_changes_nothing(conn, anna):
    a = _tx(conn, 2400, "2026-01-05", anna)
    b = _tx(conn, 1000, "2026-01-20", anna)
    first = auto_distribute_pending(conn)
    assert first.confirmed == 2 and first.review == 0
    assert _rows(conn, b) == [("2026-01", 1000, "confirmed", "auto")]
    assert _rows(conn, a) == [("2026-01", 700, "confirmed", "auto"),
                              ("2026-02", 1700, "confirmed", "auto")]
    after = _state(conn)
    assert auto_distribute_pending(conn) == AutoResult(0, 0, 0, 0)
    assert _state(conn) == after


def test_i1_repro_after_incremental_assignment(conn, anna):
    a = _tx(conn, 2400, "2026-01-05", anna)
    b = _tx(conn, 1000, "2026-01-20", anna)
    propose_allocations(conn, a)  # 1700 + 700: Vorschlag
    propose_allocations(conn, b)  # 1000 Teilzahlung im Januar
    auto_distribute_pending(conn)
    after = _state(conn)
    assert _rows(conn, a)[0][2] == "confirmed"
    assert auto_distribute_pending(conn).confirmed == 0
    assert _state(conn) == after


AMOUNTS = [1700, 3400, 1000, 700, 2000, 500, 20400, 1, 5100, 1000, 2400, 1700, 1700]


def _scenario(seed):
    rnd = random.Random(seed)
    conn = connect(":memory:")
    set_check_start(conn, f"2026-{rnd.randint(1, 3):02d}")
    members = [create_member(conn, f"M{i}", rnd.choice(["Aktiv", "Passiv"]),
                             date(2025, rnd.randint(10, 12), 5)) for i in range(rnd.randint(1, 3))]
    if rnd.random() < 0.4:
        set_fee(conn, "Aktiv", 2000, f"2026-{rnd.randint(2, 8):02d}")
    if rnd.random() < 0.4:
        leave_member(conn, rnd.choice(members), date(2026, rnd.randint(3, 10), 15))
    txs = []
    for _ in range(rnd.randint(2, 9)):
        day = f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
        if rnd.random() < 0.1:
            day = f"2025-12-{rnd.randint(1, 31):02d}"
        txs.append(_tx(conn, rnd.choice(AMOUNTS), day, rnd.choice(members)))
    order = txs[:]
    rnd.shuffle(order)
    for t in order:
        propose_allocations(conn, t)
    return conn, members, txs


def _check_invariants(conn, members, txs):
    for t in txs:
        amount = conn.execute('select amount_cents from "transaction" where id=?', (t,)).fetchone()[0]
        rows = _rows(conn, t)
        assert not rows or sum(r[1] for r in rows) == amount, (t, rows)
        assert all(r[1] > 0 for r in rows)
        assert len({r[2] for r in rows}) <= 1
    for m in members:
        for (month,) in conn.execute("select distinct month from allocation where member_id=?",
                                     (m,)).fetchall():
            paid = conn.execute("select coalesce(sum(cents),0) from allocation where member_id=?"
                                " and month=? and status='confirmed'", (m, month)).fetchone()[0]
            assert paid <= max(expected_cents(conn, m, month), 0) or paid == 0, (m, month)


@pytest.mark.parametrize("seed", range(70))
def test_i1_fuzz_fixed_point_and_sums(seed):
    conn, members, txs = _scenario(seed)
    try:
        auto_distribute_pending(conn)
        _check_invariants(conn, members, txs)
        after = _state(conn)
        assert auto_distribute_pending(conn).confirmed == 0
        assert _state(conn) == after
        redistribute_all(conn)
        _check_invariants(conn, members, txs)
        rebuilt = _state(conn)
        assert redistribute_all(conn) == (0, 0)
        assert _state(conn) == rebuilt
        assert not conn.in_transaction
    finally:
        conn.close()


# --- I2: Verteilung aufheben bleibt -------------------------------------------------

def test_i2_clear_creates_manual_proposal_that_sticks(conn, anna):
    t = _tx(conn, 1700, "2026-01-05", anna)
    propose_allocations(conn, t)
    assert clear_allocations(conn, t) == 1
    assert not conn.in_transaction
    assert _rows(conn, t) == [("2026-01", 1700, "proposed", "manual")]
    auto_distribute_pending(conn)
    redistribute_all(conn)
    assert _rows(conn, t) == [("2026-01", 1700, "proposed", "manual")]
    assert month_status(conn, anna, "2026-01", date(2026, 1, 20)).status == "OPEN"


def test_i2_clear_unlocks_member_change(conn, anna):
    bert = _m(conn, "Bert")
    t = _tx(conn, 1700, "2026-01-05", cp_iban=FAKE)
    assign_member(conn, t, anna, learn=False)
    confirm_allocations(conn, t, [("2026-01", 1700)])
    clear_allocations(conn, t)
    assign_member(conn, t, bert, learn=False)
    assert _rows(conn, t) == [("2026-01", 1700, "proposed", "manual")]  # I4 (1)


def test_i2_clear_before_check_start_no_rows(conn):
    set_check_start(conn, "2026-01")
    m = _m(conn)
    t = _tx(conn, 1700, "2026-01-05", m)
    confirm_allocations(conn, t, [("2026-01", 1700)])
    set_check_start(conn, "2026-02")
    clear_allocations(conn, t)
    assert _rows(conn, t) == []


def test_i2_clear_without_member_just_deletes(conn):
    t = _tx(conn, 1700, "2026-01-05")
    assert clear_allocations(conn, t) == 0
    assert _rows(conn, t) == []


# --- I4: Mitgliedswechsel ------------------------------------------------------------

def test_i4_sibling_repro_old_member_recomputed(conn, anna):
    bert = _m(conn, "Bert")
    conn.execute("update member set iban=? where id=?", (FAKE, anna))
    conn.commit()
    t1 = _tx(conn, 1700, "2026-01-05", cp_iban=FAKE)
    t2 = _tx(conn, 1700, "2026-01-06", cp_iban=FAKE)
    assert auto_assign(conn, [t1, t2]) == 2
    assert _rows(conn, t2) == [("2026-02", 1700, "confirmed", "auto")]
    assign_member(conn, t1, bert, learn=False)
    assert _rows(conn, t1) == [("2026-01", 1700, "confirmed", "auto")]
    assert _rows(conn, t2) == [("2026-01", 1700, "confirmed", "auto")]  # A: jetzt Januar
    assert month_status(conn, anna, "2026-02", date(2026, 2, 10)).status == "OPEN"


def test_i4_new_member_later_payments_recomputed(conn, anna):
    bert = _m(conn, "Bert")
    t3 = _tx(conn, 1700, "2026-01-20", bert)
    propose_allocations(conn, t3)
    assert _rows(conn, t3) == [("2026-01", 1700, "confirmed", "auto")]
    t1 = _tx(conn, 1700, "2026-01-05", anna)
    propose_allocations(conn, t1)
    assign_member(conn, t1, bert, learn=False)
    assert _rows(conn, t1) == [("2026-01", 1700, "confirmed", "auto")]
    assert _rows(conn, t3) == [("2026-02", 1700, "confirmed", "auto")]


def test_i4_unassign_recomputes_old_member(conn, anna):
    t1 = _tx(conn, 1700, "2026-01-05", anna)
    t2 = _tx(conn, 1700, "2026-01-06", anna)
    propose_allocations(conn, t1)
    propose_allocations(conn, t2)
    assert _rows(conn, t2)[0][0] == "2026-02"
    assign_member(conn, t1, None)
    assert _rows(conn, t1) == []
    assert _rows(conn, t2) == [("2026-01", 1700, "confirmed", "auto")]


def test_i4_manual_proposal_moves_as_manual(conn, anna):
    bert = _m(conn, "Bert")
    t = _tx(conn, 1700, "2026-01-05", anna)
    propose_allocations(conn, t)
    undo_auto(conn, t)
    assign_member(conn, t, bert, learn=False)
    assert _rows(conn, t) == [("2026-01", 1700, "proposed", "manual")]


def test_i4_manual_rows_of_others_untouched(conn, anna):
    t1 = _tx(conn, 1700, "2026-01-05", anna)
    t2 = _tx(conn, 1700, "2026-01-06", anna)
    propose_allocations(conn, t1)
    confirm_allocations(conn, t2, [("2026-02", 1700)])
    assign_member(conn, t1, None)
    assert _rows(conn, t2) == [("2026-02", 1700, "confirmed", "manual")]


# --- I6: Prüfungsstart verteilt alles Abgeleitete neu ------------------------------------

def test_i6_moving_check_start_drops_january_payment(conn, anna):
    t = _tx(conn, 3400, "2026-01-05", anna)
    propose_allocations(conn, t)
    assert len(_rows(conn, t)) == 2
    set_check_start(conn, "2026-02")
    assert redistribute_all(conn) == (0, 1)
    assert _rows(conn, t) == []
    assert month_status(conn, anna, "2026-02", date(2026, 2, 10)).status == "OPEN"


def _import_2025_then_2026(conn, set_first):
    if set_first:
        set_check_start(conn, "2026-01")
    m = create_member(conn, "Lang", "Aktiv", date(2015, 3, 1), iban=FAKE)
    ids = [_tx(conn, 1700, f"2025-{i:02d}-03", cp_iban=FAKE) for i in range(1, 13)]
    ids += [_tx(conn, 1700, f"2026-{i:02d}-03", cp_iban=FAKE) for i in range(1, 4)]
    ids.append(_tx(conn, 3400, "2026-03-20", cp_iban=FAKE))
    auto_assign(conn, ids)
    if not set_first:
        set_check_start(conn, "2026-01")
        changed, outside = redistribute_all(conn)
        assert (changed, outside) == (0, 12)
    return [_rows(conn, t) for t in ids]


def test_i6_late_check_start_equals_check_start_first():
    a, b = connect(":memory:"), connect(":memory:")
    try:
        assert _import_2025_then_2026(a, False) == _import_2025_then_2026(b, True)
    finally:
        a.close()
        b.close()


def test_i6_manual_rows_untouched_by_redistribute(conn, anna):
    t = _tx(conn, 1700, "2026-01-05", anna)
    confirm_allocations(conn, t, [("2026-03", 1700)])
    u = _tx(conn, 1700, "2026-02-05", anna)
    propose_allocations(conn, u)
    undo_auto(conn, u)
    before = [r for r in _state(conn)]
    set_check_start(conn, "2026-03")
    redistribute_all(conn)
    assert _state(conn) == before


def test_i6_counts_only_changed(conn, anna):
    t1 = _tx(conn, 1700, "2026-01-05", anna)
    t2 = _tx(conn, 1700, "2026-02-05", anna)
    for t in (t1, t2):
        propose_allocations(conn, t)
    assert redistribute_all(conn) == (0, 0)  # nichts geändert
    leave_member(conn, anna, date(2026, 1, 20))  # Februar nicht mehr fällig
    # Fix-Runde 3: der Austritt verteilt schon selbst neu, danach ist nichts mehr offen
    assert redistribute_all(conn) == (0, 0)
    assert _rows(conn, t2) == [("2026-02", 1700, "proposed", "auto")]


# --- Regel (d): Teilzahlung vervollständigt den ältesten angefangenen Monat ---------------

def test_d_rest_in_february_completes_january(conn, anna):
    jan = _tx(conn, 1000, "2026-01-15", anna)
    propose_allocations(conn, jan)
    rest = _tx(conn, 700, "2026-02-03", anna)
    assert propose_allocations(conn, rest) == [("2026-01", 700)]
    assert _rows(conn, rest) == [("2026-01", 700, "confirmed", "auto")]
    assert month_status(conn, anna, "2026-01", date(2026, 2, 10)).status == "PAID"
    assert month_status(conn, anna, "2026-02", date(2026, 2, 10)).status == "OPEN"


def test_d_rest_in_march_completes_january(conn, anna):
    propose_allocations(conn, _tx(conn, 1000, "2026-01-15", anna))
    propose_allocations(conn, _tx(conn, 1700, "2026-02-05", anna))
    rest = _tx(conn, 700, "2026-03-03", anna)
    assert propose_allocations(conn, rest) == [("2026-01", 700)]
    assert month_status(conn, anna, "2026-01", date(2026, 3, 10)).status == "PAID"
    assert month_status(conn, anna, "2026-03", date(2026, 3, 10)).status == "OPEN"


def test_d_smaller_rest_stays_partial_in_oldest(conn, anna):
    propose_allocations(conn, _tx(conn, 1000, "2026-01-15", anna))
    rest = _tx(conn, 500, "2026-02-03", anna)
    assert propose_allocations(conn, rest) == [("2026-01", 500)]
    assert _rows(conn, rest)[0][2] == "confirmed"


def test_d_larger_amount_uses_normal_rules(conn, anna):
    propose_allocations(conn, _tx(conn, 1000, "2026-01-15", anna))
    t = _tx(conn, 1700, "2026-02-03", anna)  # 1700 > 700: Buchungsmonat zuerst
    assert propose_allocations(conn, t) == [("2026-02", 1700)]


# --- Kleinigkeiten (d), (e) ---------------------------------------------------------------

def test_e_more_than_12_months_is_proposal(conn, anna):
    t = _tx(conn, 37 * 1700, "2026-01-05", anna)
    allocs = propose_allocations(conn, t)
    assert len(allocs) == 37
    assert {r[2] for r in _rows(conn, t)} == {"proposed"}
    assert review_reason(conn, t) == "Mehr als 12 Monate im Voraus"
    t13 = _tx(conn, 13 * 1700, "2026-01-05", _m(conn, "Bert"))
    propose_allocations(conn, t13)
    assert review_reason(conn, t13) == "Mehr als 12 Monate im Voraus"
    t12 = _tx(conn, 12 * 1700, "2026-01-05", _m(conn, "Carla"))
    propose_allocations(conn, t12)
    assert _rows(conn, t12)[0][2] == "confirmed"


def test_stale_auto_proposal_reason(conn, anna):
    a = _tx(conn, 2400, "2026-01-05", anna)
    propose_allocations(conn, a)  # Vorschlag
    b = _tx(conn, 1000, "2026-01-20", anna)
    propose_allocations(conn, b)  # jetzt wäre a eindeutig
    assert review_reason(conn, a) == "Wird beim nächsten Lauf automatisch verteilt"


def test_distribution_counts_with_outside(conn):
    set_check_start(conn, "2026-02")
    m = _m(conn)
    ids = [_tx(conn, 1700, "2026-01-05", m), _tx(conn, 1700, "2026-02-05", m),
           _tx(conn, 2000, "2026-03-05", m), _tx(conn, 1700, "2026-04-05")]
    for t in ids[:3]:
        propose_allocations(conn, t)
    assert dues.distribution_counts(conn, ids) == (1, 1, 1)
    assert dues.distribution_counts(conn, []) == (0, 0, 0)
