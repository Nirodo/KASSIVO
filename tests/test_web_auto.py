"""Weboberfläche der automatischen Verteilung (#27). Nur erfundene Daten."""
import html
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from kassivo import dues
from kassivo.db import connect
from kassivo.dues import confirm_allocations, propose_allocations, set_check_start
from kassivo.members import create_member
from tests.helpers import IBAN, OTHER_IBAN, make_app, make_camt, make_zip

EVIL = {"origin": "http://evil.example"}


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "t.db"


@pytest.fixture
def client(db_path):
    return TestClient(make_app(db_path), follow_redirects=False)


@pytest.fixture
def db(db_path, client):
    c = connect(db_path)
    yield c
    c.close()


def _text(page):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def _member(db, name="Erika Muster", **kw):
    return create_member(db, name, "Aktiv", date(2025, 12, 10), **kw)  # zahlt ab 2026-01


def _tx(db, member_id, amount, day, cp_name="Erika Muster", n=[0]):
    n[0] += 1
    db.execute("insert or ignore into account(id, iban) values(1, 'XX00FAKEKONTO')")
    cur = db.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, cp_name, member_id,'
        " dedupe_key) values(1,?,?,?,?,?)", (day, amount, cp_name, member_id, f"w{n[0]}"))
    db.commit()
    return cur.lastrowid


def _rows(db, tx):
    return [tuple(r) for r in db.execute(
        "select month, cents, status, source from allocation where tx_id=? order by month",
        (tx,))]


def _all(db):
    return sorted(tuple(r) for r in db.execute(
        "select tx_id, month, cents, status, source from allocation"))


def _auto_tx(db):
    set_check_start(db, "2026-01")
    m = _member(db)
    tx = _tx(db, m, 1700, "2026-03-05")
    propose_allocations(db, tx)
    return m, tx


# --- Seite "Automatisch verteilt" ----------------------------------------------------

def test_auto_page_lists_automatic_distributions(client, db):
    m, tx = _auto_tx(db)
    second = _tx(db, m, 1700, "2026-03-20")
    propose_allocations(db, second)  # März bezahlt: Januar (Rückstand)
    r = client.get("/auto")
    assert r.status_code == 200
    t = _text(r.text)
    assert "Automatisch verteilt" in t and "Erika Muster" in t
    assert "2026-03: 17,00 €" in t and "2026-01: 17,00 €" in t
    assert "Normal" in t and "Rückstand" in t
    assert t.count("Rückgängig") >= 2
    assert f'action="/auto/{tx}/undo"' in r.text
    assert "KASSIVO verteilt eindeutige Zahlungen selbst" in t
    assert '<a href="/wiki/verteilung">Hilfe zu dieser Seite</a>' in r.text
    assert r.headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
    assert "<script" not in r.text and "style=" not in r.text


def test_auto_page_empty(client):
    t = _text(client.get("/auto").text)
    assert "Noch keine automatisch verteilten Zahlungen." in t


def test_auto_page_escapes_names(client, db):
    set_check_start(db, "2026-01")
    m = _member(db, name="<b>Evil</b>")
    tx = _tx(db, m, 1700, "2026-03-05", cp_name="<script>x</script>")
    propose_allocations(db, tx)
    r = client.get("/auto")
    assert "<b>Evil</b>" not in r.text and "<script>x" not in r.text
    assert "&lt;b&gt;Evil&lt;/b&gt;" in r.text


def test_undo_turns_auto_into_manual_proposal(client, db):
    m, tx = _auto_tx(db)
    r = client.post(f"/auto/{tx}/undo")
    assert r.status_code == 303 and r.headers["location"] == "/auto?note=undone&n=1"
    assert _rows(db, tx) == [("2026-03", 1700, "proposed", "manual")]
    t = _text(client.get(r.headers["location"]).text)
    assert "Automatische Verteilung rückgängig gemacht." in t
    assert "Noch keine automatisch verteilten Zahlungen." in t
    lst = client.get("/transactions?year=all&proposed=1")
    lt = _text(lst.text)
    assert "Erika Muster" in lt and dues.REASON_MANUAL in lt
    assert f'href="/transactions/{tx}/allocate' in lst.text
    # spätere automatische Läufe bestätigen den Vorschlag nicht wieder
    client.post("/auto/run")
    client.post("/settings/fees", data={"form": "check_start", "check_start": "2026-01"})
    assert _rows(db, tx) == [("2026-03", 1700, "proposed", "manual")]


def test_undo_of_manual_confirmation_rejected(client, db):
    set_check_start(db, "2026-01")
    m = _member(db)
    tx = _tx(db, m, 1700, "2026-03-05")
    confirm_allocations(db, tx, [("2026-03", 1700)])
    before = _all(db)
    r = client.post(f"/auto/{tx}/undo")
    assert r.status_code == 422
    assert "Nur automatisch verteilte Zahlungen können rückgängig gemacht werden." in _text(r.text)
    assert _all(db) == before


@pytest.mark.parametrize("path", ["/auto/0/undo", "/auto/-1/undo", "/auto/99999/undo",
                                  "/auto/" + "9" * 20 + "/undo", "/auto/abc/undo"])
def test_undo_bad_ids_404(client, db, path):
    _auto_tx(db)
    before = _all(db)
    assert client.post(path).status_code == 404
    assert _all(db) == before


@pytest.mark.parametrize("path", ["/auto/{tx}/undo", "/auto/run"])
def test_foreign_origin_changes_nothing(client, db, path):
    m, tx = _auto_tx(db)
    _tx(db, m, 2000, "2026-04-05")  # ohne Zeilen: würde /auto/run verteilen
    before = _all(db)
    r = client.post(path.format(tx=tx), headers=EVIL)
    assert r.status_code == 403
    assert _all(db) == before


def test_auto_is_post_only_for_actions(client, db):
    m, tx = _auto_tx(db)
    assert client.get(f"/auto/{tx}/undo").status_code == 405
    assert client.get("/auto/run").status_code == 405


# --- Offene Vorschläge ----------------------------------------------------------------

def test_proposed_list_shows_reason_and_run_button(client, db):
    set_check_start(db, "2026-01")
    m = _member(db)
    tx = _tx(db, m, 2000, "2026-01-05")
    propose_allocations(db, tx)
    r = client.get("/transactions?year=all&proposed=1")
    t = _text(r.text)
    assert dues.REASON_AMOUNT in t
    assert 'action="/auto/run"' in r.text
    assert "Offene Vorschläge jetzt automatisch verteilen" in t
    assert f'href="/transactions/{tx}/allocate' in r.text
    assert dues.REASON_AMOUNT not in _text(client.get("/transactions?year=all").text)


def test_run_distributes_and_shows_note(client, db):
    set_check_start(db, "2026-02")
    m = _member(db)
    outside = _tx(db, m, 1700, "2026-01-05")
    ok = _tx(db, m, 1700, "2026-02-05")
    odd = _tx(db, m, 2000, "2026-03-05")
    r = client.post("/auto/run")
    assert r.status_code == 303
    # Zahlungen vor dem Prüfungsstart ohne Zeilen zählen nicht mit (Fix-Runde 1, f)
    assert r.headers["location"] == "/transactions?year=all&proposed=1&note=run&n=2&a=1&r=1"
    assert _rows(db, outside) == []
    assert _rows(db, ok) == [("2026-02", 1700, "confirmed", "auto")]
    assert [x[2] for x in _rows(db, odd)] == ["proposed", "proposed"]
    t = _text(client.get(r.headers["location"]).text)
    assert "2 geprüft: 1 automatisch verteilt, 1 bleiben zur Prüfung." in t
    assert "Prüfungsstart." not in t.split("2 geprüft")[1][:80]


def test_run_counts_removed_outside_proposals(client, db):
    set_check_start(db, "2026-01")
    m = _member(db)
    old = _tx(db, m, 2000, "2026-01-05")
    propose_allocations(db, old)  # Vorschlag
    set_check_start(db, "2026-02")
    r = client.post("/auto/run")
    assert r.headers["location"] == (
        "/transactions?year=all&proposed=1&note=run_outside&n=1&a=0&r=0&o=1")
    assert _rows(db, old) == []
    t = _text(client.get(r.headers["location"]).text)
    assert ("1 geprüft: 0 automatisch verteilt, 0 bleiben zur Prüfung, "
            "1 liegen vor dem Prüfungsstart.") in t


def test_check_start_note_counts_changed_and_outside(client, db):
    set_check_start(db, "2026-01")
    m = _member(db)
    jan = _tx(db, m, 3400, "2026-01-05")
    feb = _tx(db, m, 1700, "2026-02-20")  # Februar schon bezahlt: geht auf März
    for t in (jan, feb):
        propose_allocations(db, t)
    r = client.post("/settings/fees", data={"form": "check_start", "check_start": "2026-01"})
    assert r.headers["location"] == "/settings/fees?note=reproposed&n=0"
    r = client.post("/settings/fees", data={"form": "check_start", "check_start": "2026-02"})
    assert r.headers["location"] == "/settings/fees?note=reproposed_outside&n=1&o=1"
    assert _rows(db, jan) == []
    assert _rows(db, feb) == [("2026-02", 1700, "confirmed", "auto")]  # jetzt Februar
    t = _text(client.get(r.headers["location"]).text)
    assert "Prüfungsstart gespeichert. 1 Zahlungen neu verteilt, 1 liegen vor dem Prüfungsstart." in t


@pytest.mark.parametrize("query", [
    "note=run&n=3&a=%3Cscript%3E&r=1&o=1", "note=run&n=3&r=1&o=1", "note=run&n=3&a=1&r=1&o=-1",
    "note=frei&n=1", "note=run&n=3&a=12345678&r=1&o=1",
])
def test_run_note_whitelisted(client, query):
    r = client.get(f"/transactions?year=all&proposed=1&{query}")
    assert r.status_code == 200
    assert "geprüft:" not in r.text and "<script>" not in r.text


# --- Import und Beitragsübersicht ------------------------------------------------------

def test_import_result_counts_auto_and_review(client, db):
    set_check_start(db, "2026-01")
    _member(db, iban=OTHER_IBAN)
    entries = [
        {"amount": 1700, "name": "Erika Muster", "cp_iban": OTHER_IBAN, "purpose": "A", "ref": "R1"},
        {"amount": 2000, "name": "Erika Muster", "cp_iban": OTHER_IBAN, "purpose": "B", "ref": "R2"},
    ]
    data = make_zip({"a.xml": make_camt(IBAN, 10000, entries, "2026-03-01")})
    r = client.post("/import", files={"file": ("x.zip", data, "application/octet-stream")})
    assert r.status_code == 200
    t = _text(r.text)
    assert "automatisch verteilt: 1, zur Prüfung: 1" in t
    assert 'href="/auto"' in r.text
    assert 'href="/transactions?year=all&amp;proposed=1"' in r.text


def test_dues_notice_counts_waiting_payments(client, db):
    set_check_start(db, "2026-01")
    m = _member(db)
    propose_allocations(db, _tx(db, m, 2000, "2026-01-05"))
    r = client.get("/dues?year=2026")
    t = _text(r.text)
    assert "1 Zahlungen warten auf Ihre Prüfung" in t
    assert 'href="/transactions?year=all&amp;proposed=1"' in r.text
    assert 'href="/auto"' in r.text and "Automatisch verteilt" in t


def test_dues_without_waiting_payments_no_notice(client):
    t = _text(client.get("/dues").text)
    assert "warten auf Ihre Prüfung" not in t
