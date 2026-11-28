"""#56: Beitragstabelle: Zelle anklicken, bestätigen, Sammelbestätigung. Nur erfundene Daten."""
import html
import re
from datetime import date
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from kassivo import dues
from kassivo.db import connect
from kassivo.members import create_member
from kassivo.web.common import get_today
from tests.helpers import make_app

TODAY = date(2026, 11, 24)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "t.db"


@pytest.fixture
def client(db_path):
    app = make_app(db_path)
    app.dependency_overrides[get_today] = lambda: TODAY
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def db(db_path, client):
    c = connect(db_path)
    yield c
    c.close()


def _tx(db, member_id, amount, day="2026-03-05", n=[0]):
    n[0] += 1
    db.execute("insert or ignore into account(id, iban) values(1, 'DE00TEST')")
    tx = db.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, member_id,'
        " cp_name, purpose, dedupe_key) values(1,?,?,?,?,?,?)",
        (day, amount, member_id, "Erika Muster", "Beitrag", f"w{n[0]}")).lastrowid
    db.commit()
    dues.propose_allocations(db, tx)
    return tx


def _erika(db, name="Erika Muster"):
    dues.set_check_start(db, "2026-01")
    return create_member(db, name, "Aktiv", date(2026, 1, 15))  # fee_start 2026-02, 17,00


def _text(page):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def _rows(db, tx):
    return [tuple(r) for r in db.execute(
        "select month, cents, status, source from allocation where tx_id=? order by month", (tx,))]


# --- Raster ------------------------------------------------------------------------

def test_grid_cells_link_to_cell_page(client, db):
    m = _erika(db)
    _tx(db, m, 2000)
    page = client.get("/dues?year=2026").text
    assert f'href="/dues/{m}/2026-03"' in page
    assert f'href="/dues/{m}/2026-01"' not in page  # vor dem Beitragsbeginn: nichts fällig
    assert 'href="/dues/confirm-all"' in page


def test_member_history_cells_stay_plain(client, db):
    m = _erika(db)
    _tx(db, m, 2000)
    assert f'href="/dues/{m}/' not in client.get(f"/members/{m}/history?year=2026").text


# --- Zelle -------------------------------------------------------------------------

def test_cell_page_shows_proposal_with_all_months(client, db):
    m = _erika(db)
    tx = _tx(db, m, 2000)
    r = client.get(f"/dues/{m}/2026-03")
    assert r.status_code == 200
    text = _text(r.text)
    assert "Erika Muster" in text and "2026-03-05" in text and "20,00 €" in text
    assert "2026-02: 3,00 €" in text and "2026-03: 17,00 €" in text
    assert "auf 2 Monate verteilt" in text
    assert dues.REASON_AMOUNT in text
    sig = dues.proposal_signature(db, tx)
    assert f'action="/dues/{m}/2026-03/confirm"' in r.text
    assert f'value="{sig}"' in r.text
    nxt = quote(f"/dues/{m}/2026-03", safe="")
    assert f'href="/transactions/{tx}/allocate?next={nxt}"' in r.text
    assert "/undo" not in r.text


def test_cell_page_without_payment(client, db):
    m = _erika(db)
    r = client.get(f"/dues/{m}/2026-05")
    assert r.status_code == 200 and "Keine Zahlung" in r.text
    assert 'href="/dues?year=2026"' in r.text


@pytest.mark.parametrize("path", ["/dues/{m}/2026-13", "/dues/{m}/26-03", "/dues/999/2026-03",
                                  "/dues/0/2026-03"])
def test_cell_page_rejects_bad_input(client, db, path):
    m = _erika(db)
    assert client.get(path.format(m=m)).status_code == 404


def test_confirm_from_cell(client, db):
    m = _erika(db)
    tx = _tx(db, m, 2000)
    r = client.post(f"/dues/{m}/2026-03/confirm", data={"pick": dues.proposal_signature(db, tx)})
    assert r.status_code == 303
    assert r.headers["location"] == f"/dues/{m}/2026-03?note=dues_confirmed&n=1"
    assert _rows(db, tx) == [("2026-02", 300, "confirmed", "manual"),
                             ("2026-03", 1700, "confirmed", "manual")]
    assert "Bestätigt" in client.get(r.headers["location"]).text


def test_confirm_from_cell_stale_changes_nothing(client, db):
    m = _erika(db)
    tx = _tx(db, m, 2000)
    before = _rows(db, tx)
    for pick in (f"{tx}:{m}:2026-03=2000", "kaputt", ""):
        r = client.post(f"/dues/{m}/2026-03/confirm", data={"pick": pick})
        assert r.status_code == 409, pick
        assert "geändert" in r.text and f'href="/dues/{m}/2026-03"' in r.text
    assert _rows(db, tx) == before


def test_cell_page_warns_about_overpay(client, db):
    m = _erika(db)
    a = _tx(db, m, 2000)
    b = _tx(db, m, 2000, "2026-03-06")
    dues.clear_allocations(db, b)
    dues.confirm_allocations(db, a, [("2026-03", 2000)])
    text = _text(client.get(f"/dues/{m}/2026-03").text)
    assert "2026-03 wäre danach überzahlt" in text


def test_undo_auto_from_cell(client, db):
    m = _erika(db)
    tx = _tx(db, m, 1700, "2026-02-03")
    page = client.get(f"/dues/{m}/2026-02").text
    assert f'action="/dues/{m}/2026-02/undo"' in page and f'name="tx_id" value="{tx}"' in page
    assert "/confirm\"" not in page
    r = client.post(f"/dues/{m}/2026-02/undo", data={"tx_id": str(tx)})
    assert r.status_code == 303 and r.headers["location"] == f"/dues/{m}/2026-02?note=undone&n=1"
    assert _rows(db, tx) == [("2026-02", 1700, "proposed", "manual")]


def test_undo_rejected_for_manual_and_bad_ids(client, db):
    m = _erika(db)
    tx = _tx(db, m, 2000)
    dues.confirm_allocations(db, tx, [("2026-03", 2000)])
    page = client.get(f"/dues/{m}/2026-03").text
    assert "/undo" not in page and "/confirm\"" not in page and "/allocate" in page
    assert client.post(f"/dues/{m}/2026-03/undo", data={"tx_id": str(tx)}).status_code == 422
    assert client.post(f"/dues/{m}/2026-03/undo", data={"tx_id": "x"}).status_code == 422
    assert client.post(f"/dues/{m}/2026-03/undo", data={"tx_id": "999"}).status_code == 422
    assert _rows(db, tx) == [("2026-03", 2000, "confirmed", "manual")]


def test_undo_rejects_payment_of_other_member(client, db):
    m = _erika(db)
    o = _erika(db, "Otto")
    tx = _tx(db, o, 1700, "2026-02-03")
    assert client.post(f"/dues/{m}/2026-02/undo", data={"tx_id": str(tx)}).status_code == 422
    assert _rows(db, tx)[0][2] == "confirmed"


def test_allocate_returns_to_cell(client, db):
    m = _erika(db)
    tx = _tx(db, m, 2000)
    r = client.post(f"/transactions/{tx}/allocate",
                    data={"month_0": "2026-03", "amount_0": "20,00", "next": f"/dues/{m}/2026-03"})
    assert r.status_code == 303 and r.headers["location"] == f"/dues/{m}/2026-03"


# --- Alle Vorschläge bestätigen ----------------------------------------------------

def test_confirm_all_preview(client, db):
    m = _erika(db)
    a = _tx(db, m, 2000)
    b = _tx(db, m, 2000, "2026-03-06")
    r = client.get("/dues/confirm-all")
    assert r.status_code == 200
    sig_a, sig_b = dues.proposal_signature(db, a), dues.proposal_signature(db, b)
    assert re.search(rf'name="pick" value="{re.escape(sig_a)}" checked', r.text)
    assert f'name="pick" value="{sig_b}"' in r.text
    assert not re.search(rf'value="{re.escape(sig_b)}" checked', r.text)
    text = _text(r.text)
    assert dues.REASON_AMOUNT in text and "Erika Muster" in text
    assert "2026-03 wäre überzahlt" in text
    assert 'action="/dues/confirm-all"' in r.text


def test_confirm_all_preview_empty(client, db):
    _erika(db)
    assert "Keine Vorschläge" in client.get("/dues/confirm-all").text


def test_confirm_all_post_reports_skipped(client, db):
    m = _erika(db)
    a = _tx(db, m, 2000)
    b = _tx(db, m, 2000, "2026-03-06")
    picks = [dues.proposal_signature(db, a), dues.proposal_signature(db, b)]
    r = client.post("/dues/confirm-all", data={"pick": picks})
    assert r.status_code == 200
    text = _text(r.text)
    assert "1 Vorschlag bestätigt" in text
    assert "1 übersprungen" in text and "2026-03-06" in text
    assert _rows(db, a)[0][2] == "confirmed" and _rows(db, b)[0][2] == "proposed"


def test_confirm_all_post_nothing_selected(client, db):
    m = _erika(db)
    a = _tx(db, m, 2000)
    r = client.post("/dues/confirm-all", data={})
    assert r.status_code == 200 and "Nichts ausgewählt" in r.text
    assert _rows(db, a)[0][2] == "proposed"


def test_confirm_all_post_resubmit_is_harmless(client, db):
    m = _erika(db)
    a = _tx(db, m, 2000)
    data = {"pick": [dues.proposal_signature(db, a)]}
    client.post("/dues/confirm-all", data=data)
    after = _rows(db, a)
    text = _text(client.post("/dues/confirm-all", data=data).text)
    assert "0 Vorschläge bestätigt" in text and "1 übersprungen" in text
    assert _rows(db, a) == after


def test_confirm_all_rejects_cross_site(client, db):
    m = _erika(db)
    a = _tx(db, m, 2000)
    r = client.post("/dues/confirm-all", data={"pick": [dues.proposal_signature(db, a)]},
                    headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403 and _rows(db, a)[0][2] == "proposed"
