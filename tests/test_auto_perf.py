"""Fix-Runde 2 zu #27: schnelle Berechnung mit Zwischenspeicher, gleiche Ergebnisse.

Die Referenz unten ist die frühere, einfache Fassung von dues._compute (jede Zahl
per Datenbankabfrage). Sie dient als Orakel: Mit Zwischenspeicher müssen alle
Zeilen (Monat, Betrag, Status, Herkunft) genau gleich sein. Nur erfundene Daten.
"""
import random
import time
from datetime import date

import pytest

from kassivo import dues
from kassivo.db import connect
from kassivo.dues import (
    _Plan, auto_distribute_pending, clear_allocations, confirm_allocations, propose_allocations,
    redistribute_all, set_check_start, undo_auto,
)
from kassivo.fees import set_fee
from kassivo.matching import assign_member, auto_assign
from kassivo.members import create_member, expected_cents, leave_member, set_member_type
from kassivo.months import add_months, month_range


# --- Referenz (frühere Fassung, nur Abfragen) ----------------------------------------------

def _ref_paid(conn, member_id, month, exclude_tx):
    return conn.execute(
        "select coalesce(sum(cents), 0) from allocation where member_id=? and month=?"
        " and status='confirmed' and tx_id<>?", (member_id, month, exclude_tx)).fetchone()[0]


def ref_compute(conn, tx, ctx=None):
    amount, booking_month = tx["amount_cents"], tx["booking_date"][:7]
    check_start = dues.get_check_start(conn)
    fee_start = conn.execute("select fee_start from member where id=?",
                             (tx["member_id"],)).fetchone()[0]
    bounds = [m for m in (check_start, fee_start) if m]
    start = max(bounds) if bounds else booking_month
    if check_start and booking_month < check_start:
        return _Plan([], outside=True)

    def short(month):
        return (expected_cents(conn, tx["member_id"], month),
                _ref_paid(conn, tx["member_id"], month, tx["id"]))

    if booking_month > start:
        for month in month_range(start, add_months(booking_month, -1)):
            expected, paid = short(month)
            if 0 < paid < expected and amount <= expected - paid:
                return _Plan([(month, amount)])
    order = ([booking_month] if booking_month >= start else []) + [
        m for m in month_range(start, add_months(max(start, booking_month), dues.MAX_MONTHS_AHEAD))
        if m != booking_month]
    allocs, remaining, full, first = {}, amount, True, None
    for month in order:
        if remaining == 0:
            break
        expected, paid = short(month)
        shortfall = expected - paid
        if shortfall <= 0:
            continue
        first = shortfall if first is None else first
        take = min(shortfall, remaining)
        full = full and take == shortfall
        allocs[month] = take
        remaining -= take
    reason = None
    if not allocs:
        reason = dues.REASON_NO_MONTH
    elif remaining:
        reason = dues.REASON_REST
    elif not full and amount >= first:
        reason = dues.REASON_AMOUNT
    elif len(allocs) > dues.MAX_AUTO_MONTHS:
        reason = dues.REASON_TOO_MANY
    if remaining:
        last = max(allocs) if allocs else booking_month
        allocs[last] = allocs.get(last, 0) + remaining
    return _Plan(sorted(allocs.items()), reason=reason)


# --- Szenarien -------------------------------------------------------------------------

def _state(conn):
    return sorted(tuple(r) for r in conn.execute(
        "select tx_id, member_id, month, cents, status, source from allocation"))


def _play(seed):
    """Spielt ein Zufallsszenario ab und gibt den Zustand nach jedem Schritt zurück."""
    rnd = random.Random(seed)
    conn = connect(":memory:")
    conn.execute("insert into account(id, iban) values(1, 'XX00FAKEKONTO')")
    set_check_start(conn, f"2026-{rnd.randint(1, 3):02d}")
    members = [create_member(conn, f"M{i}", rnd.choice(["Aktiv", "Passiv"]),
                             date(2025, rnd.randint(9, 12), 5)) for i in range(rnd.randint(1, 3))]
    if rnd.random() < 0.5:
        set_fee(conn, "Aktiv", 2000, f"2026-{rnd.randint(2, 9):02d}")
    if rnd.random() < 0.4:
        set_member_type(conn, members[0], "Passiv", f"2026-{rnd.randint(3, 9):02d}")
    if rnd.random() < 0.4:
        leave_member(conn, rnd.choice(members), date(2026, rnd.randint(3, 11), 15))
    states = []
    n = 0
    for _ in range(25):
        op = rnd.choice(["pay", "pay", "pay", "undo", "clear", "confirm", "move", "start", "run"])
        txs = [r[0] for r in conn.execute('select id from "transaction"')]
        if op == "pay" or not txs:
            n += 1
            cur = conn.execute(
                'insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key)'
                " values(1,?,?,?)", (f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}",
                                    rnd.choice([1700, 3400, 1000, 700, 2000, 1, 20400, 2400, 5100]),
                                    f"perf{n}"))
            conn.commit()
            assign_member(conn, cur.lastrowid, rnd.choice(members), learn=False)
        elif op == "undo":
            auto = [r[0] for r in conn.execute(
                "select distinct tx_id from allocation where source='auto' and status='confirmed'")]
            if auto:
                undo_auto(conn, rnd.choice(auto))
        elif op == "clear":
            clear_allocations(conn, rnd.choice(txs))
        elif op == "confirm":
            props = [r[0] for r in conn.execute(
                "select distinct tx_id from allocation where status='proposed'")]
            if props:
                t = rnd.choice(props)
                rows = [(r[0], r[1]) for r in conn.execute(
                    "select month, cents from allocation where tx_id=?", (t,))]
                confirm_allocations(conn, t, rows)
        elif op == "move":
            try:
                assign_member(conn, rnd.choice(txs), rnd.choice(members + [None]), learn=False)
            except ValueError:
                pass
        elif op == "start":
            set_check_start(conn, f"2026-{rnd.randint(1, 4):02d}")
            redistribute_all(conn)
        else:
            auto_distribute_pending(conn)
        states.append(_state(conn))
    conn.close()
    return states


@pytest.mark.parametrize("seed", range(60))
def test_cached_equals_reference(seed, monkeypatch):
    fast = _play(seed)
    monkeypatch.setattr(dues, "_compute", ref_compute)
    slow = _play(seed)
    assert fast == slow


def test_compute_plan_equals_reference_per_payment():
    for seed in range(15):
        rnd = random.Random(500 + seed)
        conn = connect(":memory:")
        conn.execute("insert into account(id, iban) values(1, 'XX00FAKEKONTO')")
        set_check_start(conn, "2026-01")
        m = create_member(conn, "M", "Aktiv", date(2025, 12, 5))
        for i in range(12):
            cur = conn.execute(
                'insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key,'
                " member_id) values(1,?,?,?,?)",
                (f"2026-{rnd.randint(1, 12):02d}-05", rnd.choice([1700, 1000, 700, 3400]),
                 f"pp{seed}-{i}", m))
            conn.commit()
            propose_allocations(conn, cur.lastrowid)
        for (t,) in conn.execute('select id from "transaction"').fetchall():
            tx = conn.execute('select id, amount_cents, member_id, booking_date from "transaction"'
                              " where id=?", (t,)).fetchone()
            assert dues._compute(conn, tx) == ref_compute(conn, tx)
        conn.close()


# --- Laufzeit (grobe Schranke, damit ein Rückfall auffällt) -----------------------------------

def test_long_member_400_payments_is_fast(tmp_path):
    conn = connect(tmp_path / "p.db")
    set_check_start(conn, "2016-01")
    conn.execute("insert into account(id, iban) values(1, 'XX00FAKEKONTO')")
    create_member(conn, "Lang", "Aktiv", date(2015, 3, 1), iban="XX00FAKE0000000001")
    ids = []
    for i in range(400):
        y, mo = divmod(i, 12)
        ids.append(conn.execute(
            'insert into "transaction"(account_id, booking_date, amount_cents, cp_iban,'
            " dedupe_key) values(1,?,?,?,?)",
            (f"{2016 + y:04d}-{mo + 1:02d}-03", 3400 if i % 17 == 0 else 1700,
             "XX00FAKE0000000001", f"l{i}")).lastrowid)
    conn.commit()
    t = time.perf_counter()
    auto_assign(conn, ids)
    redistribute_all(conn)
    assert time.perf_counter() - t < 5  # Ziel unter 1 s je Lauf, großzügig für langsame CI
    conn.close()
