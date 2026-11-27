"""#7: Import, Regeln, Zuordnung und Neuverteilung in einer Transaktion. Erfundene Daten."""
import html
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from kassivo import categories, dues, matching
from kassivo.categories import add_rule, create_category
from kassivo.db import connect
from kassivo.ingest import import_and_assign
from kassivo.members import create_member
from tests.helpers import IBAN, OTHER_IBAN, make_app, make_camt, make_zip

TABLES = ("account", "import_batch", "transaction", "allocation", "member_iban", "settings")


def _state(conn):
    return {t: sorted(tuple(r) for r in conn.execute(f'select * from "{t}"')) for t in TABLES}


def _zip(tmp_path, entries, day="2026-03-02", name="a.zip"):
    p = tmp_path / name
    p.write_bytes(make_zip({"a.xml": make_camt(IBAN, 10000, entries, day)}))
    return p


E_ERIKA = {"amount": 1700, "name": "Erika Muster", "cp_iban": OTHER_IBAN,
           "purpose": "Beitrag", "ref": "R1"}
E_ZINS = {"amount": 12, "name": "Bank", "purpose": "Zinsen", "ref": "R2"}
E_MINUS = {"amount": -500, "name": "Kiosk", "purpose": "Material", "ref": "R3"}


@pytest.fixture
def setup(conn):
    m = create_member(conn, "Erika Muster", "Aktiv", date(2025, 12, 10), iban=OTHER_IBAN)
    cat = create_category(conn, "Zinsen", "in")
    add_rule(conn, cat, "purpose", "Zinsen")
    conn.commit()
    return m, cat


def test_success_categorizes_assigns_and_distributes(conn, tmp_path, setup):
    m, cat = setup
    out = import_and_assign(conn, _zip(tmp_path, [E_ERIKA, E_ZINS, E_MINUS]))
    assert (out.result.new, out.result.duplicates) == (3, 0)
    assert out.categorized == 1 and out.assigned == 1
    assert not conn.in_transaction
    erika = conn.execute('select member_id, id from "transaction" where purpose=?',
                         ("Beitrag",)).fetchone()
    assert erika["member_id"] == m
    assert conn.execute("select count(*) from allocation where tx_id=?",
                        (erika["id"],)).fetchone()[0] == 1
    assert conn.execute('select category_id from "transaction" where purpose=?',
                        ("Zinsen",)).fetchone()[0] == cat


@pytest.mark.parametrize("where", ["rules", "assign", "rebuild", "redistribute"])
def test_failure_in_any_step_leaves_nothing(conn, tmp_path, setup, monkeypatch, where):
    """Fehler mitten im Import: kein Teilzustand, auch kein Konto und kein Stapel."""
    def boom(*a, **k):
        raise RuntimeError("Testfehler")

    target = {"rules": (categories, "apply_rules"), "assign": (matching, "auto_assign"),
              "rebuild": (dues, "rebuild_member"),
              "redistribute": (dues, "redistribute_all")}[where]
    before = _state(conn)
    monkeypatch.setattr(*target, boom)
    with pytest.raises(RuntimeError):
        import_and_assign(conn, _zip(tmp_path, [E_ERIKA, E_ZINS, E_MINUS]))
    assert not conn.in_transaction
    assert _state(conn) == before
    monkeypatch.undo()
    # Wiederholung danach: alles neu, nichts als Duplikat
    out = import_and_assign(conn, _zip(tmp_path, [E_ERIKA, E_ZINS, E_MINUS]))
    assert (out.result.new, out.result.duplicates) == (3, 0)
    assert out.assigned == 1 and out.categorized == 1


def test_failure_after_partial_writes_of_assign_rolls_back(conn, tmp_path, setup, monkeypatch):
    """auto_assign hat schon member_id geschrieben, dann Fehler in der Verteilung."""
    calls = []
    real = dues.rebuild_member

    def late_boom(c, member_id, exclude=None):
        real(c, member_id, exclude)
        calls.append(member_id)
        raise RuntimeError("Testfehler")

    before = _state(conn)
    monkeypatch.setattr(dues, "rebuild_member", late_boom)
    with pytest.raises(RuntimeError):
        import_and_assign(conn, _zip(tmp_path, [E_ERIKA]))
    assert calls and _state(conn) == before


def test_reimport_is_idempotent(conn, tmp_path, setup):
    p = _zip(tmp_path, [E_ERIKA, E_ZINS, E_MINUS])
    import_and_assign(conn, p)
    after_first = _state(conn)
    out = import_and_assign(conn, p)
    assert (out.result.new, out.result.duplicates) == (0, 3)
    assert out.categorized == 0 and out.assigned == 0 and not out.redistributed
    second = _state(conn)
    # nur ein weiterer Stapel mit 0 neuen Buchungen, sonst unverändert
    assert second["import_batch"][:-1] == after_first["import_batch"]
    assert second["import_batch"][-1][3:] == (0, 3)
    second.pop("import_batch"), after_first.pop("import_batch")
    assert second == after_first


def test_reimport_keeps_manual_decisions(conn, tmp_path, setup):
    m, _ = setup
    import_and_assign(conn, _zip(tmp_path, [E_ERIKA]))
    tx = conn.execute('select id from "transaction"').fetchone()[0]
    dues.confirm_allocations(conn, tx, [("2026-02", 1700)])
    manual = sorted(tuple(r) for r in conn.execute("select * from allocation"))
    import_and_assign(conn, _zip(tmp_path, [E_ERIKA]))
    assert sorted(tuple(r) for r in conn.execute("select * from allocation")) == manual


def test_older_file_moves_derived_start_in_same_commit(conn, tmp_path, setup):
    import_and_assign(conn, _zip(tmp_path, [dict(E_ERIKA, amount=3400, ref="M")], "2026-03-02"))
    out = import_and_assign(conn, _zip(tmp_path, [dict(E_ERIKA, ref="J")], "2026-01-05",
                                       name="b.zip"))
    assert out.redistributed
    assert dues.get_check_start(conn) == "2026-01"


def test_stored_start_no_redistribute(conn, tmp_path, setup, monkeypatch):
    dues.set_check_start(conn, "2026-01")
    calls = []
    monkeypatch.setattr(dues, "redistribute_all", lambda *a, **k: calls.append(1))
    out = import_and_assign(conn, _zip(tmp_path, [E_ERIKA], "2025-11-05"))
    assert calls == [] and not out.redistributed


def test_inside_open_transaction_refused(conn, tmp_path, setup):
    conn.execute("insert into settings(key, value) values('x', 'y')")
    with pytest.raises(RuntimeError):
        import_and_assign(conn, _zip(tmp_path, [E_ERIKA]))


def test_auto_assign_without_commit_leaves_transaction_open(conn, setup):
    m, _ = setup
    conn.execute("insert into account(id, iban) values(1, 'XX00')")
    t = conn.execute('insert into "transaction"(account_id, booking_date, amount_cents, cp_iban,'
                     " dedupe_key) values(1, '2026-03-02', 1700, ?, 'k')", (OTHER_IBAN,)).lastrowid
    conn.commit()
    assert matching.auto_assign(conn, [t], commit=False) == 1
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute('select member_id from "transaction"').fetchone()[0] is None


def test_redistribute_all_without_commit_leaves_transaction_open(conn, setup):
    conn.execute("insert into settings(key, value) values('x', 'y')")
    dues.redistribute_all(conn, commit=False)
    assert conn.in_transaction
    conn.rollback()


# --- Oberfläche --------------------------------------------------------------------------

def _text(page):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


@pytest.fixture
def web(tmp_path):
    p = tmp_path / "t.db"
    client = TestClient(make_app(p), follow_redirects=False)
    c = connect(p)
    create_member(c, "Erika Muster", "Aktiv", date(2025, 12, 10), iban=OTHER_IBAN)
    c.commit()
    yield client, c
    c.close()


def _post(client, entries, day="2026-03-02"):
    data = make_zip({"a.xml": make_camt(IBAN, 10000, entries, day)})
    return client.post("/import", files={"file": ("x.zip", data, "application/octet-stream")})


def test_web_failure_shows_error_and_retry_works(web, monkeypatch):
    client, c = web

    def boom(*a, **k):
        raise RuntimeError("geheim DE00000000000000000000")

    monkeypatch.setattr(matching, "auto_assign", boom)
    r = _post(client, [E_ERIKA])
    assert r.status_code == 500
    text = _text(r.text)
    assert "Es wurde nichts importiert" in text and "erneut" in text
    assert "geheim" not in r.text and OTHER_IBAN not in r.text
    assert 'action="/import"' in r.text  # Formular zum Wiederholen
    assert c.execute('select count(*) from "transaction"').fetchone()[0] == 0
    assert c.execute("select count(*) from import_batch").fetchone()[0] == 0
    monkeypatch.undo()
    r = _post(client, [E_ERIKA])
    assert r.status_code == 200
    text = _text(r.text)
    assert "Neu: 1" in text and "Duplikate: 0" in text and "Automatisch zugeordnet: 1" in text


def test_web_redistribute_failure_imports_nothing(web, monkeypatch):
    client, c = web
    assert _post(client, [dict(E_ERIKA, ref="Z1")]).status_code == 200

    def boom(*a, **k):
        raise RuntimeError("Testfehler")

    monkeypatch.setattr(dues, "redistribute_all", boom)
    r = _post(client, [dict(E_ERIKA, ref="Z2")], "2026-01-02")
    assert r.status_code == 500
    assert c.execute('select count(*) from "transaction"').fetchone()[0] == 1
