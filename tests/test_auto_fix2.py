"""Fix-Runde 2 zu #27: spätere automatische Zahlungen folgen Entscheidungen. Erfundene Daten."""
import random
import sqlite3
from datetime import date

import pytest

from kassivo import dues
from kassivo.db import connect
from kassivo.dues import (
    auto_distribute_pending, clear_allocations, confirm_allocations, month_status,
    propose_allocations, redistribute_all, set_check_start, undo_auto,
)
from kassivo.matching import assign_member, auto_assign
from kassivo.members import create_member, expected_cents, leave_member

FAKE = "XX00FAKE0000000001"


def _tx(conn, amount, day, member_id=None, cp_iban=None, n=[0]):
    n[0] += 1
    conn.execute("insert or ignore into account(id, iban) values(1, 'XX00FAKEKONTO')")
    cur = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, cp_iban,'
        " member_id, dedupe_key) values(1,?,?,?,?,?)",
        (day, amount, cp_iban, member_id, f"f2x{n[0]}"))
    conn.commit()
    return cur.lastrowid


def _rows(conn, tx):
    return [tuple(r) for r in conn.execute(
        "select month, cents, status, source from allocation where tx_id=? order by month, id",
        (tx,))]


def _state(conn):
    return sorted(tuple(r) for r in conn.execute(
        "select tx_id, member_id, month, cents, status, source from allocation"))


def _manual(conn):
    return sorted(tuple(r) for r in conn.execute(
        "select tx_id, member_id, month, cents, status from allocation where source='manual'"))


def _no_overallocation(conn):
    for r in conn.execute("select member_id, month, sum(cents) as s from allocation"
                          " where status='confirmed' group by member_id, month").fetchall():
        assert r["s"] <= expected_cents(conn, r["member_id"], r["month"]), tuple(r)


@pytest.fixture
def anna(conn):
    set_check_start(conn, "2026-01")
    return create_member(conn, "Anna", "Aktiv", date(2025, 12, 10))


# --- Repros --------------------------------------------------------------------------

def _t1_t2(conn, anna):
    t1 = _tx(conn, 3400, "2026-01-05", anna)
    t2 = _tx(conn, 1700, "2026-02-03", anna)
    propose_allocations(conn, t1)
    propose_allocations(conn, t2)
    assert _rows(conn, t2) == [("2026-03", 1700, "confirmed", "auto")]
    return t1, t2


def test_confirm_moves_later_auto_payment(conn, anna):
    t1, t2 = _t1_t2(conn, anna)
    confirm_allocations(conn, t1, [("2026-01", 1700), ("2026-03", 1700)])
    assert not conn.in_transaction
    assert _rows(conn, t2) == [("2026-02", 1700, "confirmed", "auto")]
    _no_overallocation(conn)
    assert month_status(conn, anna, "2026-02", date(2026, 3, 10)).status == "PAID"


def test_undo_then_confirm_stays_consistent(conn, anna):
    t1, t2 = _t1_t2(conn, anna)
    undo_auto(conn, t1)
    assert _rows(conn, t2) == [("2026-02", 1700, "confirmed", "auto")]  # kanonisch
    proposal = [(m, c) for m, c, _s, _src in _rows(conn, t1)]
    assert proposal == [("2026-01", 1700), ("2026-03", 1700)]
    confirm_allocations(conn, t1, proposal)
    _no_overallocation(conn)
    assert _rows(conn, t2) == [("2026-02", 1700, "confirmed", "auto")]


def test_undo_then_confirm_overlapping_month_moves_auto(conn, anna):
    t1, t2 = _t1_t2(conn, anna)
    undo_auto(conn, t1)
    confirm_allocations(conn, t1, [("2026-01", 1700), ("2026-02", 1700)])
    _no_overallocation(conn)
    assert _rows(conn, t2) == [("2026-03", 1700, "confirmed", "auto")]


def test_clear_moves_later_auto_payment(conn, anna):
    t1, t2 = _t1_t2(conn, anna)
    clear_allocations(conn, t1)
    assert _rows(conn, t2) == [("2026-02", 1700, "confirmed", "auto")]
    assert [r[2:] for r in _rows(conn, t1)] == [("proposed", "manual")] * 2


def test_confirm_moves_earlier_auto_payment_too(conn, anna):
    early = _tx(conn, 1700, "2026-01-05", anna)
    propose_allocations(conn, early)
    late = _tx(conn, 1700, "2026-02-05", anna)
    confirm_allocations(conn, late, [("2026-01", 1700)])  # Nutzer: späte Zahlung für Januar
    _no_overallocation(conn)
    assert _rows(conn, early) == [("2026-02", 1700, "confirmed", "auto")]


# --- Fuzz: jede Operation endet im kanonischen Zustand ----------------------------------

def _canonical(conn):
    copy = sqlite3.connect(":memory:")
    conn.backup(copy)
    copy.row_factory = sqlite3.Row
    try:
        redistribute_all(copy)
        return _state(copy)
    finally:
        copy.close()


def _invariants(conn):
    assert not conn.in_transaction
    for t in [r[0] for r in conn.execute("select distinct tx_id from allocation")]:
        amount = conn.execute('select amount_cents from "transaction" where id=?', (t,)).fetchone()[0]
        rows = _rows(conn, t)
        assert sum(r[1] for r in rows) == amount
        assert all(r[1] > 0 for r in rows)
        assert len({(r[2], r[3]) for r in rows}) == 1, rows
    _no_overallocation(conn)


def _safe_to_confirm(conn, tx_id):
    tx = conn.execute('select id, amount_cents, member_id, booking_date from "transaction"'
                      " where id=?", (tx_id,)).fetchone()
    plan = dues._compute(conn, tx)
    return (not plan.outside and plan.reason not in (dues.REASON_REST, dues.REASON_NO_MONTH)
            and plan.allocs)


def _op(conn, rnd, members):
    txs = [r[0] for r in conn.execute('select id from "transaction" where amount_cents > 0')]
    kind = rnd.choice(["pay", "pay", "undo", "clear", "confirm", "move", "unassign",
                       "check_start", "run"])
    automatic = kind in ("run", "check_start")
    before = _manual(conn)
    if kind == "pay":
        t = _tx(conn, rnd.choice([1700, 3400, 1000, 700, 2000, 500, 20400, 2400]),
                f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}")
        assign_member(conn, t, rnd.choice(members), learn=False)
    elif kind == "undo":
        auto = [r[0] for r in conn.execute("select distinct tx_id from allocation"
                                           " where status='confirmed' and source='auto'")]
        if auto:
            undo_auto(conn, rnd.choice(auto))
    elif kind == "clear" and txs:
        clear_allocations(conn, rnd.choice(txs))
    elif kind == "confirm":
        cand = [r[0] for r in conn.execute("select distinct tx_id from allocation"
                                           " where status='proposed'")]
        if cand:
            t = rnd.choice(cand)
            if _safe_to_confirm(conn, t):
                propose_allocations(conn, t, auto=False)
                confirm_allocations(conn, t, [(m, c) for m, c, *_ in _rows(conn, t)])
    elif kind in ("move", "unassign") and txs:
        t = rnd.choice(txs)
        target = None if kind == "unassign" else rnd.choice(members)
        try:
            assign_member(conn, t, target, learn=False)
        except ValueError:
            pass  # bestätigte Verteilung des Nutzers sperrt
    elif kind == "check_start":
        set_check_start(conn, f"2026-{rnd.randint(1, 4):02d}")
        redistribute_all(conn)
    elif kind == "run":
        auto_distribute_pending(conn)
    if automatic:
        assert _manual(conn) == before, kind
    return kind


@pytest.mark.parametrize("seed", range(40))
def test_fuzz_operations_stay_canonical(seed):
    rnd = random.Random(1000 + seed)
    conn = connect(":memory:")
    try:
        set_check_start(conn, "2026-01")
        members = [create_member(conn, f"M{i}", rnd.choice(["Aktiv", "Passiv"]),
                                 date(2025, 12, 5)) for i in range(rnd.randint(1, 3))]
        if rnd.random() < 0.3:
            leave_member(conn, members[0], date(2026, rnd.randint(4, 11), 10))
        for _ in range(30):
            kind = _op(conn, rnd, members)
            _invariants(conn)
            assert _state(conn) == _canonical(conn), kind
            snap = _state(conn)
            res = auto_distribute_pending(conn)
            assert res.confirmed == 0 and _state(conn) == snap, kind
    finally:
        conn.close()


# --- Import verschiebt den abgeleiteten Prüfungsstart ---------------------------------------

def test_import_older_file_with_derived_start(tmp_path):
    from tests.helpers import IBAN, make_app, make_camt, make_zip
    from fastapi.testclient import TestClient
    p = tmp_path / "t.db"
    client = TestClient(make_app(p), follow_redirects=False)
    conn = connect(p)
    try:
        create_member(conn, "B", "Aktiv", date(2025, 12, 10), iban=FAKE)

        def up(entries, day):
            data = make_zip({"a.xml": make_camt(IBAN, 10000, entries, day)})
            r = client.post("/import", files={"file": ("x.zip", data, "application/octet-stream")})
            assert r.status_code == 200

        up([{"amount": 3400, "name": "B", "cp_iban": FAKE, "purpose": "März", "ref": "M1"}],
           "2026-03-02")
        assert dues.get_check_start(conn) == "2026-03"
        up([{"amount": 1700, "name": "B", "cp_iban": FAKE, "purpose": "Jan", "ref": "J1"}],
           "2026-01-05")
        assert dues.get_check_start(conn) == "2026-01"
        assert _state(conn) == _canonical(conn)
        march = conn.execute("select id from \"transaction\" where purpose='März'").fetchone()[0]
        assert _rows(conn, march) == [("2026-02", 1700, "confirmed", "auto"),
                                      ("2026-03", 1700, "confirmed", "auto")]
    finally:
        conn.close()


def test_import_older_file_of_other_member_moves_derived_start(tmp_path):
    from tests.helpers import IBAN, OTHER_IBAN, make_app, make_camt, make_zip
    from fastapi.testclient import TestClient
    p = tmp_path / "t.db"
    client = TestClient(make_app(p), follow_redirects=False)
    conn = connect(p)
    try:
        create_member(conn, "B", "Aktiv", date(2025, 12, 10), iban=FAKE)
        create_member(conn, "C", "Aktiv", date(2025, 12, 10), iban=OTHER_IBAN)

        def up(entries, day):
            data = make_zip({"a.xml": make_camt(IBAN, 10000, entries, day)})
            assert client.post("/import", files={
                "file": ("x.zip", data, "application/octet-stream")}).status_code == 200

        up([{"amount": 3400, "name": "B", "cp_iban": FAKE, "purpose": "März", "ref": "M1"}],
           "2026-03-02")
        up([{"amount": 1700, "name": "C", "cp_iban": OTHER_IBAN, "purpose": "Jan", "ref": "J1"}],
           "2026-01-05")
        assert _state(conn) == _canonical(conn)
        march = conn.execute("select id from \"transaction\" where purpose='März'").fetchone()[0]
        assert [r[0] for r in _rows(conn, march)] == ["2026-01", "2026-03"]  # März, dann Januar
    finally:
        conn.close()


def test_import_with_stored_start_does_not_redistribute(tmp_path, monkeypatch):
    from tests.helpers import IBAN, make_app, make_camt, make_zip
    from fastapi.testclient import TestClient
    p = tmp_path / "t.db"
    client = TestClient(make_app(p), follow_redirects=False)
    conn = connect(p)
    set_check_start(conn, "2026-01")
    calls = []
    monkeypatch.setattr(dues, "redistribute_all", lambda c: calls.append(1) or (0, 0))
    data = make_zip({"a.xml": make_camt(IBAN, 10000, [{"amount": 1700, "ref": "Z"}], "2025-11-05")})
    assert client.post("/import", files={"file": ("x.zip", data, "application/octet-stream")}
                       ).status_code == 200
    conn.close()
    assert calls == []
