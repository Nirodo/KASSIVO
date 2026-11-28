"""Mitgliederzuordnung mit Lernen. Nur erfundene Daten."""
from datetime import date

import pytest

from kassivo import dues
from kassivo.categories import create_category
from kassivo.dues import month_status, set_check_start
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
    t2 = _tx(conn, 1700, MOM)
    # ein vorhandener gelernter Eintrag hat beim Nachschlagen Vorrang (#55:
    # so entsteht er auch durch Zuordnen an B mit "Absender merken").
    conn.execute("insert into member_iban(iban, member_id) values(?,?)", (MOM, b))
    conn.commit()
    assert auto_assign(conn, [t2]) == 1
    assert _row(conn, t2)["member_id"] == b
    assert _row(conn, t2)["member_id"] != a


def test_ambiguous_iban_skipped(conn):
    _member(conn, "A", iban=KID)
    _member(conn, "B", iban="de33333333333333333333")
    t = _tx(conn, 1700, KID)
    assert auto_assign(conn, [t]) == 0
    assert _row(conn, t)["member_id"] is None


def test_second_member_overrides_learned_iban(conn):
    # #55: die letzte Entscheidung gilt (bis 1.2.0: IBAN wurde mehrdeutig)
    a, b = _member(conn, "A"), _member(conn, "B")
    t1, t2, t3 = _tx(conn, 1700, MOM), _tx(conn, 1700, MOM), _tx(conn, 1700, MOM)
    assign_member(conn, t1, a)
    assert assign_member(conn, t2, b) == "changed"
    assert _learned(conn) == {MOM: b}
    assert auto_assign(conn, [t3]) == 1
    assert _row(conn, t3)["member_id"] == b


def test_ambiguous_learned_iban_no_fall_through_to_member_iban(conn):
    _member(conn, "A", iban=MOM)
    conn.execute("insert into member_iban(iban, member_id) values(?, null)", (MOM,))
    conn.commit()
    t3 = _tx(conn, 1700, MOM)
    assert auto_assign(conn, [t3]) == 0  # nicht auf member.iban von A


def test_ambiguous_iban_stays_ambiguous_on_later_learn(conn):
    a = _member(conn, "A")
    conn.execute("insert into member_iban(iban, member_id) values(?, null)", (MOM,))
    conn.commit()
    t1, t2 = _tx(conn, 1700, MOM), _tx(conn, 1700, MOM)
    assert assign_member(conn, t1, a) == "multiple"
    assert _learned(conn) == {MOM: None}
    assert auto_assign(conn, [t2]) == 0


def test_same_member_again_keeps_mapping(conn):
    a = _member(conn, "A")
    t1, t2, t3 = _tx(conn, 1700, MOM), _tx(conn, 1700, MOM), _tx(conn, 1700, MOM)
    assign_member(conn, t1, a)
    assign_member(conn, t2, a)
    assert _learned(conn) == {MOM: a}
    assert auto_assign(conn, [t3]) == 1


def test_learn_false_does_not_make_ambiguous(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    t1, t2, t3 = _tx(conn, 1700, MOM), _tx(conn, 1700, MOM), _tx(conn, 1700, MOM)
    assign_member(conn, t1, a)
    assign_member(conn, t2, b, learn=False)
    assert _learned(conn) == {MOM: a}
    assert auto_assign(conn, [t3]) == 1
    assert _row(conn, t3)["member_id"] == a


def test_siblings_one_parent_two_children_end_to_end(conn):
    """Ein Elternteil zahlt für zwei Kinder: nach "zahlt für mehrere" bleibt
    alles in Unklar (#55: bewusste Auswahl auf "Gemerkte Zuordnungen")."""
    from kassivo.matching import entry_check, remembered, set_remembered
    dues.set_check_start(conn, "2026-01")
    a, b = _member(conn, "Kind A"), _member(conn, "Kind B")
    t1 = _tx(conn, 1700, "XX00FAKEPARENT", day="2026-01-03")
    t2 = _tx(conn, 1700, "XX00FAKEPARENT", day="2026-01-04")
    conn.commit()
    assign_member(conn, t1, a)
    assert assign_member(conn, t2, b) == "changed"
    r0 = remembered(conn)[0]
    set_remembered(conn, r0["id"], None, entry_check(r0["iban"]))
    feb = [_tx(conn, 1700, "XX00FAKEPARENT", day="2026-02-03"),
           _tx(conn, 1700, "XX00FAKEPARENT", day="2026-02-04")]
    conn.commit()
    assert auto_assign(conn, feb) == 0
    assert set(feb) <= set(_ids(unmatched(conn)))
    assert conn.execute(
        "select count(*) from allocation where tx_id in (?,?)", feb).fetchone()[0] == 0


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


def _clear(conn, tx_id):
    # assign_member legt seit Task 7 selbst Vorschläge an. Tests mit
    # handgebauten Zeilen starten danach von einem leeren Stand.
    conn.execute("delete from allocation where tx_id=?", (tx_id,))


def _full(conn, tx_id):
    return [
        (r["member_id"], r["month"], r["cents"], r["status"])
        for r in conn.execute(
            "select member_id, month, cents, status from allocation"
            " where tx_id=? order by month, id", (tx_id,))
    ]


def test_unassign_keeps_category_and_learned_iban(conn):
    m = _member(conn)
    t = _tx(conn, 2000, MOM)  # passt nicht zu ganzen Monaten: nur Vorschlag
    assign_member(conn, t, m)
    assign_member(conn, t, None)
    assert _row(conn, t)["member_id"] is None
    assert _row(conn, t)["category_id"] == _beitraege(conn)
    assert _learned(conn) == {MOM: m}
    assert t in _ids(unmatched(conn))
    assert _allocs(conn, t) == []


def test_unassign_deletes_proposed_allocations(conn):
    m = _member(conn)
    t = _tx(conn, 2000, MOM)
    assign_member(conn, t, m)
    _alloc(conn, t, m, "proposed")
    _alloc(conn, t, m, "proposed")
    assign_member(conn, t, None)
    assert _allocs(conn, t) == []


def test_confirmed_allocation_blocks_unassign(conn):
    m = _member(conn)
    t = _tx(conn, 1700, MOM)
    assign_member(conn, t, m)
    _clear(conn, t)
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
    _clear(conn, t)
    _alloc(conn, t, m, "proposed")
    _alloc(conn, t, m, "confirmed")
    assign_member(conn, t, m)
    assert _row(conn, t)["member_id"] == m
    assert _allocs(conn, t) == ["proposed", "confirmed"]


def test_reassign_other_member_deletes_proposed(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    t = _tx(conn, 2000, MOM)
    assign_member(conn, t, a)
    _alloc(conn, t, a, "proposed")
    assign_member(conn, t, b)
    assert _row(conn, t)["member_id"] == b
    # alte Vorschläge von A weg, neuer Vorschlag für B
    assert _full(conn, t) == [(b, "2026-01", 1700, "proposed"), (b, "2026-02", 300, "proposed")]


def test_reassign_other_member_confirmed_still_raises(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    t = _tx(conn, 1700, MOM)
    assign_member(conn, t, a)
    _clear(conn, t)
    _alloc(conn, t, a, "proposed")
    _alloc(conn, t, a, "confirmed")
    with pytest.raises(ValueError, match="Verteilung zuerst aufheben"):
        assign_member(conn, t, b)
    assert _allocs(conn, t) == ["proposed", "confirmed"]


def test_assign_from_none_deletes_stale_proposed(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    t = _tx(conn, 1700, MOM)
    _alloc(conn, t, a, "proposed")
    conn.execute("update allocation set source='auto' where tx_id=?", (t,))  # Systemvorschlag
    assign_member(conn, t, b)
    assert _full(conn, t) == [(b, "2026-01", 1700, "confirmed")]


# --- Vorschläge beim Zuordnen (Task 7) -------------------------------------

def test_assign_exact_amount_confirms_earliest_due_month(conn):
    m = _member(conn)  # fee_start 2025-02, Prüfungsstart Default 2026-03
    t = _tx(conn, 1700, MOM, day="2026-03-04")
    assign_member(conn, t, m)
    assert _full(conn, t) == [(m, "2026-03", 1700, "confirmed")]
    st = month_status(conn, m, "2026-03", date(2026, 3, 4))
    assert st.status == "PAID"


def test_assign_exact_amount_prefers_booking_month_over_arrears(conn):
    set_check_start(conn, "2026-01")
    m = _member(conn)
    t = _tx(conn, 1700, MOM, day="2026-03-04")
    assign_member(conn, t, m)
    assert _full(conn, t) == [(m, "2026-03", 1700, "confirmed")]  # #27: Buchungsmonat zuerst


def test_assign_double_amount_two_months_auto(conn):
    m = _member(conn)
    t = _tx(conn, 3400, MOM)
    assign_member(conn, t, m)
    assert _full(conn, t) == [
        (m, "2026-01", 1700, "confirmed"), (m, "2026-02", 1700, "confirmed")]
    today = date(2026, 1, 15)
    assert month_status(conn, m, "2026-01", today).status == "PAID"
    assert month_status(conn, m, "2026-02", today).status == "PAID"


def test_reassign_proposals_follow_new_member(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    t = _tx(conn, 2000, MOM)  # Vorschlag (passt nicht zu ganzen Monaten)
    assign_member(conn, t, a)
    assign_member(conn, t, b)
    assert _full(conn, t) == [
        (b, "2026-01", 1700, "proposed"), (b, "2026-02", 300, "proposed")]


def test_reassign_same_member_does_not_repropose(conn):
    m = _member(conn)
    t = _tx(conn, 3400, MOM)
    assign_member(conn, t, m)
    ids = [r["id"] for r in conn.execute(
        "select id from allocation where tx_id=?", (t,))]
    assign_member(conn, t, m)
    assert [r["id"] for r in conn.execute(
        "select id from allocation where tx_id=?", (t,))] == ids


def test_assign_propose_error_changes_nothing(conn, monkeypatch):
    m = _member(conn)
    t = _tx(conn, 1700, MOM)
    conn.commit()

    def boom(*a, **k):
        raise ValueError("kaputt")

    monkeypatch.setattr(dues, "propose_allocations", boom)
    with pytest.raises(ValueError):
        assign_member(conn, t, m)
    assert _row(conn, t)["member_id"] is None
    assert _row(conn, t)["category_id"] is None
    assert _learned(conn) == {}
    assert _allocs(conn, t) == []


def test_auto_assign_distributes(conn):
    m = _member(conn, iban=KID)
    t1 = _tx(conn, 1700, KID, day="2026-01-03")
    t2 = _tx(conn, 3400, KID, day="2026-02-03")
    assert auto_assign(conn, [t1, t2]) == 2
    conn.rollback()  # alles ist schon committet
    assert _full(conn, t1) == [(m, "2026-01", 1700, "confirmed")]
    assert _full(conn, t2) == [
        (m, "2026-02", 1700, "confirmed"), (m, "2026-03", 1700, "confirmed")]


def test_auto_assign_oldest_booking_first(conn):
    m = _member(conn, iban=KID)
    late = _tx(conn, 1700, KID, day="2026-02-03")
    early = _tx(conn, 1700, KID, day="2026-01-03")
    assert auto_assign(conn, [late, early]) == 2
    assert _full(conn, early) == [(m, "2026-01", 1700, "confirmed")]
    assert _full(conn, late) == [(m, "2026-02", 1700, "confirmed")]


def test_auto_assign_propose_error_changes_nothing(conn, monkeypatch):
    _member(conn, iban=KID)
    t = _tx(conn, 1700, KID)
    conn.commit()

    def boom(*a, **k):
        raise ValueError("kaputt")

    monkeypatch.setattr(dues, "propose_allocations", boom)
    with pytest.raises(ValueError):
        auto_assign(conn, [t])
    assert _row(conn, t)["member_id"] is None
    assert _row(conn, t)["category_id"] is None


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


# --- Fix-Runde 2 (N1): Mehrdeutigkeit auch über member.iban ---------------------

def test_learn_other_member_than_member_iban_owner_overrides(conn):
    """sim3 D: nur A hat die IBAN in den Stammdaten, Zuordnung an B mit Lernen.
    #55: die letzte Entscheidung gilt, der Eintrag geht vor member.iban."""
    set_check_start(conn, "2025-01")
    a = _member(conn, "A", iban="XX00FAKEPARENT")
    b = _member(conn, "B")
    t1 = _tx(conn, 1700, "XX00FAKEPARENT", day="2025-01-03")
    t2 = _tx(conn, 1700, "XX00FAKEPARENT", day="2025-01-04")
    conn.commit()
    assert auto_assign(conn, [t1]) == 1 and _row(conn, t1)["member_id"] == a
    assert assign_member(conn, t2, b, learn=True) == "changed"
    assert _row(conn, t2)["member_id"] == b
    assert _learned(conn) == {"XX00FAKEPARENT": b}
    t3 = _tx(conn, 1700, "XX00FAKEPARENT", day="2025-02-03")
    conn.commit()
    assert auto_assign(conn, [t3]) == 1
    assert _row(conn, t3)["member_id"] == b


def test_learn_on_member_iban_shared_by_siblings_stores_nothing(conn):
    a = _member(conn, "Kind A", iban=MOM)
    b = _member(conn, "Kind B", iban=MOM.lower())
    t1, t2, t3 = _tx(conn, 1700, MOM), _tx(conn, 1700, MOM), _tx(conn, 1700, MOM)
    assert assign_member(conn, t1, a, learn=True) == "multiple"
    assert _learned(conn) == {}
    assert auto_assign(conn, [t2, t3]) == 0
    assert assign_member(conn, t2, a, learn=True) == "multiple"
    assert _learned(conn) == {}
    assert auto_assign(conn, [t3]) == 0
    assert b != a


def test_learn_member_iban_owner_keeps_mapping(conn):
    a = _member(conn, "A", iban=MOM)
    t1, t2 = _tx(conn, 1700, MOM), _tx(conn, 1700, MOM)
    assign_member(conn, t1, a, learn=True)
    assert _learned(conn) == {MOM: a}
    assert auto_assign(conn, [t2]) == 1
    assert _row(conn, t2)["member_id"] == a


def test_learn_learned_owner_wins_over_member_iban_for_same_member(conn):
    # gelernter Eintrag hat Vorrang; dasselbe Mitglied erneut bleibt eindeutig
    a, b = _member(conn, "A"), _member(conn, "B", iban=MOM)
    t1, t2, t3 = _tx(conn, 1700, MOM), _tx(conn, 1700, MOM), _tx(conn, 1700, MOM)
    conn.execute("insert into member_iban(iban, member_id) values(?,?)", (MOM, a))
    conn.commit()
    assign_member(conn, t1, a, learn=True)
    assert _learned(conn) == {MOM: a}
    # anderes Mitglied als der Eintrag: die letzte Entscheidung gilt (#55)
    assert assign_member(conn, t2, b, learn=True) == "changed"
    assert _learned(conn) == {MOM: b}
    assert auto_assign(conn, [t3]) == 1 and _row(conn, t3)["member_id"] == b


def test_learn_false_writes_nothing_even_with_member_iban(conn):
    a = _member(conn, "A", iban=MOM)
    b = _member(conn, "B", iban=MOM)
    t1, t2 = _tx(conn, 1700, MOM), _tx(conn, 1700, MOM)
    assign_member(conn, t1, a, learn=False)
    assign_member(conn, t2, b, learn=False)
    assert _learned(conn) == {}


# --- Issue #28: kategorisierte Eingänge ohne Mitglied sind erledigt -------------

def _cat_tx(conn, category_id, amount=1700, iban=None):
    from kassivo.categories import set_category
    t = _tx(conn, amount, iban)
    set_category(conn, t, category_id)
    return t


def test_unmatched_hides_categorized_non_member_booking(conn):
    zins = create_category(conn, "Zinsen", "in")
    done = _cat_tx(conn, zins)
    plain = _tx(conn, 646)
    beitrag = _cat_tx(conn, _beitraege(conn))
    ids = _ids(unmatched(conn))
    assert done not in ids
    assert plain in ids and beitrag in ids


def test_unmatched_outgoing_and_member_assigned_never(conn):
    m = _member(conn)
    out = _tx(conn, -500)
    assigned = _tx(conn, 1700, MOM)
    assign_member(conn, assigned, m)
    ids = _ids(unmatched(conn))
    assert out not in ids and assigned not in ids


def test_unmatched_newest_first(conn):
    old = _tx(conn, 100, day="2026-01-01")
    new = _tx(conn, 100, day="2026-09-30")
    assert _ids(unmatched(conn)) == [new, old]


def test_unmatched_returns_after_category_removed(conn):
    from kassivo.categories import set_category
    zins = create_category(conn, "Zinsen", "in")
    t = _cat_tx(conn, zins)
    assert t not in _ids(unmatched(conn))
    set_category(conn, t, None)
    assert t in _ids(unmatched(conn))


def test_auto_assign_skips_categorized_non_member(conn):
    m = _member(conn, iban=KID)
    zins = create_category(conn, "Zinsen", "in")
    done = _cat_tx(conn, zins, iban=KID)
    plain = _tx(conn, 1700, KID)
    beitrag = _cat_tx(conn, _beitraege(conn), iban=KID)
    assert auto_assign(conn, [done, plain, beitrag]) == 2
    assert _row(conn, done)["member_id"] is None
    assert _row(conn, plain)["member_id"] == m
    assert _row(conn, beitrag)["member_id"] == m
