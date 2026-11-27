"""#6: "— keins —" ist eine Entscheidung des Nutzers und bleibt. Erfundene Daten."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from kassivo import dues
from kassivo.db import connect
from kassivo.ingest import import_and_assign
from kassivo.matching import assign_member, auto_assign, unmatched
from kassivo.members import create_member
from tests.helpers import IBAN, make_app, make_camt, make_zip

MOM = "DE22222222222222222222"


def _tx(conn, amount, cp_iban=MOM, day="2026-01-05", n=[0]):
    n[0] += 1
    conn.execute("insert or ignore into account(id, iban) values(1, 'DE00TEST')")
    t = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, cp_iban, dedupe_key)'
        " values(1,?,?,?,?)", (day, amount, cp_iban, f"nm{n[0]}")).lastrowid
    conn.commit()
    return t


def _member_of(conn, t):
    return conn.execute('select member_id from "transaction" where id=?', (t,)).fetchone()[0]


def _flag(conn, t):
    return conn.execute('select no_member from "transaction" where id=?', (t,)).fetchone()[0]


@pytest.fixture
def anna(conn):
    dues.set_check_start(conn, "2026-01")
    return create_member(conn, "Anna", "Aktiv", date(2025, 12, 10), iban=MOM)


@pytest.fixture
def spende(conn, anna):
    """Spende von Annas IBAN: erst automatisch Anna, dann vom Nutzer auf "— keins —"."""
    t = _tx(conn, 5000)
    assert auto_assign(conn, [t]) == 1
    assign_member(conn, t, None)
    return t


def test_new_transaction_has_no_flag(conn, anna):
    assert _flag(conn, _tx(conn, 1700)) == 0


def test_keins_sets_flag_and_member_clears_it(conn, anna, spende):
    assert _member_of(conn, spende) is None and _flag(conn, spende) == 1
    assign_member(conn, spende, anna, learn=False)
    assert _member_of(conn, spende) == anna and _flag(conn, spende) == 0
    assign_member(conn, spende, None)
    assert _flag(conn, spende) == 1


def test_keins_on_unassigned_payment_also_protects(conn, anna):
    t = _tx(conn, 5000)
    assign_member(conn, t, None)
    assert _flag(conn, t) == 1
    assert auto_assign(conn, [t]) == 0 and _member_of(conn, t) is None


def test_auto_assign_skips_keins(conn, anna, spende):
    assert auto_assign(conn, [spende]) == 0
    assert _member_of(conn, spende) is None and _flag(conn, spende) == 1


def test_keins_stays_in_unklar_list(conn, anna, spende):
    # Ruling: die Zahlung bleibt in "Unklar" sichtbar; erledigt wird sie über eine Kategorie
    assert spende in [r["id"] for r in unmatched(conn)]


def test_keins_survives_learning_cascade(conn, anna, spende):
    other = _tx(conn, 1700, day="2026-02-03")
    later = _tx(conn, 1700, day="2026-03-03")
    assign_member(conn, other, anna, learn=True)
    assert auto_assign(conn, [r["id"] for r in unmatched(conn)]) == 1  # nur "later"
    assert _member_of(conn, later) == anna
    assert _member_of(conn, spende) is None


@pytest.mark.parametrize("step", ["redistribute", "pending", "rebuild", "check_start"])
def test_keins_survives_redistribution(conn, anna, spende, step):
    _tx(conn, 1700, day="2026-02-03")
    auto_assign(conn, [r["id"] for r in unmatched(conn)])
    {"redistribute": lambda: dues.redistribute_all(conn),
     "pending": lambda: dues.auto_distribute_pending(conn),
     "rebuild": lambda: (dues.rebuild_member(conn, anna), conn.commit()),
     "check_start": lambda: dues.set_check_start_redistribute(conn, "2025-12")}[step]()
    assert _member_of(conn, spende) is None and _flag(conn, spende) == 1
    assert conn.execute("select count(*) from allocation where tx_id=?",
                        (spende,)).fetchone()[0] == 0


def test_keins_survives_import(conn, anna, tmp_path):
    entries = [{"amount": 5000, "name": "Anna", "cp_iban": MOM, "purpose": "Spende", "ref": "S"},
               {"amount": 1700, "name": "Anna", "cp_iban": MOM, "purpose": "Beitrag", "ref": "B"}]
    p = tmp_path / "a.zip"
    p.write_bytes(make_zip({"a.xml": make_camt(IBAN, 0, entries, "2026-01-05")}))
    assert import_and_assign(conn, p).assigned == 2
    s = conn.execute('select id from "transaction" where purpose=?', ("Spende",)).fetchone()[0]
    assign_member(conn, s, None)
    # Wiederholung derselben Datei und neue Datei derselben IBAN
    assert import_and_assign(conn, p).assigned == 0
    q = tmp_path / "b.zip"
    q.write_bytes(make_zip({"a.xml": make_camt(IBAN, 0, [dict(entries[1], ref="B2")],
                                              "2026-02-05")}))
    assert import_and_assign(conn, q).assigned == 1
    assert _member_of(conn, s) is None and _flag(conn, s) == 1


def test_keins_with_unknown_tx_or_outgoing_rejected_without_flag(conn, anna):
    out = _tx(conn, -500)
    with pytest.raises(ValueError):
        assign_member(conn, out, None)
    assert _flag(conn, out) == 0
    with pytest.raises(ValueError):
        assign_member(conn, 999, None)


# --- Oberfläche --------------------------------------------------------------------------

@pytest.fixture
def web(tmp_path):
    p = tmp_path / "t.db"
    client = TestClient(make_app(p), follow_redirects=False)
    c = connect(p)
    dues.set_check_start(c, "2026-01")
    m = create_member(c, "Anna", "Aktiv", date(2025, 12, 10), iban=MOM)
    c.commit()
    yield client, c, m
    c.close()


def test_web_keins_survives_unklar_auto_and_learning(web):
    client, c, m = web
    spende, beitrag = _tx(c, 5000), _tx(c, 1700, day="2026-02-03")
    auto_assign(c, [spende])
    r = client.post(f"/transactions/{spende}/member", data={"member_id": "", "next": "/unmatched"})
    assert r.status_code == 303
    assert client.post("/unmatched/auto").status_code == 303
    assert _member_of(c, spende) is None and _member_of(c, beitrag) == m
    r = client.post(f"/transactions/{beitrag}/member",
                    data={"member_id": str(m), "learn": "on", "next": "/unmatched"})
    assert r.status_code == 303
    assert _member_of(c, spende) is None and _flag(c, spende) == 1


def test_start_page_unklar_count_matches_unmatched_list(conn, anna, spende):
    from kassivo.overview import overview
    _tx(conn, 1700, cp_iban=None, day="2026-02-03")  # ohne IBAN, bleibt unklar
    data = overview(conn, date(2026, 3, 1))
    assert data["unmatched"] == len(unmatched(conn)) == 2  # inkl. "— keins —"
