"""Merken (#55): die letzte Entscheidung gilt. Nur erfundene Daten."""
from datetime import date

import pytest

from kassivo.matching import (
    assign_member, auto_assign, entry_check, forget, remembered, set_remembered,
)
from kassivo.members import create_member

PARENT = "XX00FAKEPARENT0000000001"
CK = entry_check(PARENT)


def _tx(conn, amount=1700, cp_iban=PARENT, day="2026-01-05", n=[0]):
    n[0] += 1
    conn.execute("insert or ignore into account(id, iban) values(1, 'XX00FAKEOWN')")
    cur = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents,'
        " cp_iban, dedupe_key) values(1,?,?,?,?)",
        (day, amount, cp_iban, f"merk{n[0]}"),
    )
    conn.commit()
    return cur.lastrowid


def _member(conn, name, iban=None):
    return create_member(conn, name, "Aktiv", date(2025, 1, 1), iban=iban)


def _learned(conn):
    return {r["iban"]: r["member_id"]
            for r in conn.execute("select iban, member_id from member_iban")}


def _member_of(conn, tx):
    return conn.execute('select member_id from "transaction" where id=?',
                        (tx,)).fetchone()[0]


# --- Regeln beim Lernen ---------------------------------------------------------

def test_first_learn_is_new(conn):
    a = _member(conn, "A")
    assert assign_member(conn, _tx(conn), a) == "new"
    assert _learned(conn) == {PARENT: a}


def test_same_member_again_is_same(conn):
    a = _member(conn, "A")
    assign_member(conn, _tx(conn), a)
    assert assign_member(conn, _tx(conn), a) == "same"
    assert _learned(conn) == {PARENT: a}


def test_last_decision_wins_and_is_changed(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    assign_member(conn, _tx(conn), a)
    assert assign_member(conn, _tx(conn), b) == "changed"
    assert _learned(conn) == {PARENT: b}
    t = _tx(conn)
    assert auto_assign(conn, [t]) == 1 and _member_of(conn, t) == b


def test_correction_back_to_a(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    assign_member(conn, _tx(conn), a)
    assign_member(conn, _tx(conn), b)
    assert assign_member(conn, _tx(conn), a) == "changed"
    assert _learned(conn) == {PARENT: a}


def test_master_data_owner_counts_as_effective(conn):
    a, b = _member(conn, "A", iban=PARENT), _member(conn, "B")
    assert assign_member(conn, _tx(conn), a) == "same"
    assert assign_member(conn, _tx(conn), b) == "changed"
    assert _learned(conn) == {PARENT: b}


def test_null_entry_stays_null(conn):
    a = _member(conn, "A")
    conn.execute("insert into member_iban(iban, member_id) values(?, null)", (PARENT,))
    conn.commit()
    assert assign_member(conn, _tx(conn), a) == "multiple"
    assert _learned(conn) == {PARENT: None}


def test_two_master_data_hits_store_nothing(conn):
    _member(conn, "Kind A", iban=PARENT)
    b = _member(conn, "Kind B", iban=PARENT.lower())
    assert assign_member(conn, _tx(conn), b) == "multiple"
    assert _learned(conn) == {}


def test_entry_beats_two_master_data_hits(conn):
    a = _member(conn, "Kind A", iban=PARENT)
    b = _member(conn, "Kind B", iban=PARENT)
    conn.execute("insert into member_iban(iban, member_id) values(?,?)", (PARENT, a))
    conn.commit()
    assert assign_member(conn, _tx(conn), b) == "changed"
    assert _learned(conn) == {PARENT: b}


def test_learn_false_changes_nothing(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    assign_member(conn, _tx(conn), a)
    assert assign_member(conn, _tx(conn), b, learn=False) is None
    assert _learned(conn) == {PARENT: a}


def test_keins_changes_nothing(conn):
    a = _member(conn, "A")
    assign_member(conn, _tx(conn), a)
    assert assign_member(conn, _tx(conn), None) is None
    assert _learned(conn) == {PARENT: a}


def test_without_iban_nothing_learned(conn):
    a = _member(conn, "A")
    assert assign_member(conn, _tx(conn, cp_iban=None), a) is None
    assert _learned(conn) == {}


def test_failed_assign_learns_nothing(conn):
    a = _member(conn, "A")
    with pytest.raises(ValueError):
        assign_member(conn, _tx(conn, amount=-500), a)
    with pytest.raises(ValueError):
        assign_member(conn, _tx(conn, amount=0), a)
    with pytest.raises(ValueError):
        assign_member(conn, _tx(conn), 9999)
    assert _learned(conn) == {}


# --- Pflege -------------------------------------------------------------------

def _entry_id(conn, iban=PARENT):
    return conn.execute("select rowid from member_iban where iban=?", (iban,)).fetchone()[0]


def test_remembered_lists_entries(conn):
    a = _member(conn, "Anna")
    assign_member(conn, _tx(conn), a)
    conn.execute("insert into member_iban(iban, member_id) values('XX00FAKEOTHER', null)")
    conn.commit()
    rows = {r["iban"]: (r["id"], r["member_id"], r["member_name"]) for r in remembered(conn)}
    assert rows == {
        PARENT: (_entry_id(conn), a, "Anna"),
        "XX00FAKEOTHER": (_entry_id(conn, "XX00FAKEOTHER"), None, None),
    }


def test_set_remembered_member_and_multiple(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    t = _tx(conn)
    assign_member(conn, t, a)
    eid = _entry_id(conn)
    set_remembered(conn, eid, b, CK)
    assert _learned(conn) == {PARENT: b}
    assert _member_of(conn, t) == a  # ordnet keine Zahlungen um
    set_remembered(conn, eid, None, CK)
    assert _learned(conn) == {PARENT: None}
    t2 = _tx(conn)
    assert auto_assign(conn, [t2]) == 0  # zahlt für mehrere: nicht selbst zuordnen


def test_null_from_wrong_correction_can_be_fixed(conn):
    """Alt-Daten: NULL durch Fehlkorrektur, auf /zuordnungen behebbar."""
    a = _member(conn, "A")
    conn.execute("insert into member_iban(iban, member_id) values(?, null)", (PARENT,))
    conn.commit()
    set_remembered(conn, _entry_id(conn), a, CK)
    t = _tx(conn)
    assert auto_assign(conn, [t]) == 1 and _member_of(conn, t) == a


def test_forget(conn):
    a = _member(conn, "A")
    t = _tx(conn)
    assign_member(conn, t, a)
    forget(conn, _entry_id(conn), CK)
    assert _learned(conn) == {}
    assert _member_of(conn, t) == a
    t2 = _tx(conn)
    assert auto_assign(conn, [t2]) == 0


def test_forget_falls_back_to_master_data(conn):
    a, b = _member(conn, "A", iban=PARENT), _member(conn, "B")
    assign_member(conn, _tx(conn), b)
    forget(conn, _entry_id(conn), CK)
    t = _tx(conn)
    assert auto_assign(conn, [t]) == 1 and _member_of(conn, t) == a


def test_unknown_entry_or_member_raises(conn):
    a = _member(conn, "A")
    assign_member(conn, _tx(conn), a)
    eid = _entry_id(conn)
    with pytest.raises(ValueError):
        set_remembered(conn, eid + 100, a, CK)
    with pytest.raises(ValueError):
        set_remembered(conn, eid, 9999, CK)
    with pytest.raises(ValueError):
        forget(conn, eid + 100, CK)
    forget(conn, eid, CK)
    with pytest.raises(ValueError):
        forget(conn, eid, CK)  # doppelt gesendet
    with pytest.raises(ValueError):
        set_remembered(conn, eid, a, CK)


def test_error_texts_hide_iban(conn):
    a = _member(conn, "A")
    assign_member(conn, _tx(conn), a)
    eid = _entry_id(conn)
    with pytest.raises(ValueError) as e:
        set_remembered(conn, eid, 9999, CK)
    assert PARENT not in str(e.value)


# --- Prüfkennung (Fix-Runde I1): rowid allein reicht nicht -----------------------

def test_entry_check_is_sha256_hex_not_iban():
    import hashlib
    assert CK == hashlib.sha256(PARENT.encode("ascii")).hexdigest()
    assert PARENT not in CK


def test_wrong_check_changes_nothing(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    assign_member(conn, _tx(conn), a)
    eid = _entry_id(conn)
    other = entry_check("XX00FAKEOTHER")
    with pytest.raises(ValueError):
        set_remembered(conn, eid, b, other)
    with pytest.raises(ValueError):
        set_remembered(conn, eid, b, "")
    with pytest.raises(ValueError):
        forget(conn, eid, other)
    assert _learned(conn) == {PARENT: a}


def test_reused_rowid_is_rejected(conn):
    """Eintrag vergessen, neuer Absender bekommt dieselbe rowid: altes Formular trifft nicht."""
    a = _member(conn, "A")
    assign_member(conn, _tx(conn), a)
    eid = _entry_id(conn)
    forget(conn, eid, CK)
    conn.execute("insert into member_iban(rowid, iban, member_id) values(?, 'XX00FAKEOTHER', ?)",
                 (eid, a))
    conn.commit()
    with pytest.raises(ValueError):
        set_remembered(conn, eid, None, CK)
    with pytest.raises(ValueError):
        forget(conn, eid, CK)
    assert _learned(conn) == {"XX00FAKEOTHER": a}


# --- Ausgetretene Mitglieder (Fix-Runde Minor 4) --------------------------------

def _left(conn, member_id, day):
    conn.execute("update member set austritt=? where id=?", (day, member_id))
    conn.commit()


def test_assign_to_member_left_in_past_learns_nothing(conn):
    a, b = _member(conn, "A"), _member(conn, "B")
    assign_member(conn, _tx(conn), a)
    _left(conn, b, "2026-01-31")
    t = _tx(conn, day="2026-03-05")
    assert assign_member(conn, t, b) is None  # Zuordnung ja, Merken nein
    assert _member_of(conn, t) == b
    assert _learned(conn) == {PARENT: a}


def test_assign_to_left_member_does_not_create_entry(conn):
    b = _member(conn, "B")
    _left(conn, b, "2025-12-31")
    assert assign_member(conn, _tx(conn), b) is None
    assert _learned(conn) == {}


def test_member_leaving_today_still_learns(conn):
    a = _member(conn, "A")
    _left(conn, a, date.today().isoformat())
    assert assign_member(conn, _tx(conn), a) == "new"
    assert _learned(conn) == {PARENT: a}


def test_member_leaving_in_future_learns(conn):
    a = _member(conn, "A")
    _left(conn, a, "2099-12-31")
    assert assign_member(conn, _tx(conn), a) == "new"
