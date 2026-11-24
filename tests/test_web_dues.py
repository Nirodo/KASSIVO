"""Weboberfläche: Beitragsübersicht, Zahlungshistorie, Prognose. Nur erfundene Daten."""
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from kassivo.db import connect
from kassivo.dues import confirm_allocations, set_check_start
from kassivo.members import create_member
from tests.helpers import make_app
from kassivo.web.common import get_today

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


def _tx(db, member_id, amount, day="2026-03-05", purpose="Beitrag", cp="Erika Muster", n=[0]):
    n[0] += 1
    db.execute("insert or ignore into account(id, iban) values(1, 'DE00TEST')")
    cur = db.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, member_id,'
        " cp_name, purpose, dedupe_key) values(1,?,?,?,?,?,?)",
        (day, amount, member_id, cp, purpose, f"k{n[0]}"))
    db.commit()
    return cur.lastrowid


def _erika(db, name="Erika Muster", eintritt=date(2026, 1, 15)):
    return create_member(db, name, "Aktiv", eintritt)  # fee_start 2026-02


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# --- /dues -------------------------------------------------------------------

def test_dues_grid_states(client, db):
    m = _erika(db)
    tx = _tx(db, m, 1700)
    confirm_allocations(db, tx, [("2026-02", 1700)])
    tx2 = _tx(db, m, 1000, day="2026-04-05")
    confirm_allocations(db, tx2, [("2026-03", 1000)])
    r = client.get("/dues")
    assert r.status_code == 200
    html = r.text
    assert "Erika Muster" in html
    assert "Bezahlt" in html and "Überfällig" in html and "—" in html
    assert "10,00 / 17,00" in _text(html)
    assert "status-none" in html and "status-overdue" in html
    assert "<caption>" in html and 'scope="col"' in html and 'scope="row"' in html
    for lbl in ("Jan", "Mär", "Dez"):
        assert lbl in html
    assert f"/members/{m}/history?year=2026" in html


def test_dues_future_month_open(client, db):
    _erika(db)
    html = client.get("/dues?year=2027").text
    assert "<td class=\"status status-open\"" in html and "<td class=\"status status-overdue\"" not in html


def test_dues_totals_and_nav(client, db):
    a = _erika(db)
    create_member(db, "Paul Passiv", "Passiv", date(2026, 1, 15))
    set_check_start(db, "2026-01")
    tx = _tx(db, a, 1700)
    confirm_allocations(db, tx, [("2026-02", 1700)])
    html = client.get("/dues?year=2026").text
    # Februar: erwartet 17+10 = 27, bezahlt 17
    assert "17,00 / 27,00" in _text(html)
    assert 'href="/dues?year=2025"' in html and 'href="/dues?year=2027"' in html


@pytest.mark.parametrize("year,missing", [(1900, "year=1899"), (2100, "year=2101")])
def test_dues_nav_boundary(client, year, missing):
    assert missing not in client.get(f"/dues?year={year}").text


@pytest.mark.parametrize("year", ["1899", "abc", "2101", "", "1e3"])
def test_dues_bad_year(client, year):
    r = client.get(f"/dues?year={year}")
    assert r.status_code == 422 and "Jahr" in r.text


def test_dues_empty(client):
    html = client.get("/dues").text
    assert "Keine Mitglieder" in html and 'href="/members/new"' in html


def test_dues_name_escaped(client, db):
    _erika(db, name="<script>alert(1)</script>")
    html = client.get("/dues").text
    assert "<script>alert" not in html and "&lt;script&gt;" in html


# --- Historie ----------------------------------------------------------------

def test_history_shows_payment(client, db):
    m = _erika(db)
    tx = _tx(db, m, 1700, purpose="Beitrag Feb")
    confirm_allocations(db, tx, [("2026-02", 1000), ("2026-03", 700)])
    r = client.get(f"/members/{m}/history?year=2026")
    assert r.status_code == 200
    t = _text(r.text)
    assert "Erika Muster" in t and "2026-01-15" in t and "Aktiv" in t
    assert "2026-03-05" in t and "17,00" in t and "Beitrag Feb" in t
    assert "2026-02: 10,00 € (bestätigt)" in t and "2026-03: 7,00 € (bestätigt)" in t
    assert f'href="/transactions/{tx}/allocate"' in r.text
    assert "Überfällig" in t and "<caption>" in r.text


def test_history_empty(client, db):
    m = _erika(db)
    assert "Keine Zahlungen" in _text(client.get(f"/members/{m}/history").text)


def test_history_newest_first(client, db):
    m = _erika(db)
    _tx(db, m, 100, day="2026-03-01", purpose="ALT")
    _tx(db, m, 100, day="2026-05-01", purpose="NEU")
    html = client.get(f"/members/{m}/history").text
    assert html.index("NEU") < html.index("ALT")


@pytest.mark.parametrize("mid", ["999", "0", "-1", "9" * 20])
def test_history_unknown_404(client, mid):
    r = client.get(f"/members/{mid}/history")
    assert r.status_code == 404 and "nicht gefunden" in r.text


def test_history_bad_year(client, db):
    m = _erika(db)
    assert client.get(f"/members/{m}/history?year=1899").status_code == 422


def test_history_escapes(client, db):
    m = _erika(db)
    _tx(db, m, 100, purpose="<b>x</b>", cp="<i>y</i>")
    html = client.get(f"/members/{m}/history").text
    assert "<b>x</b>" not in html and "<i>y</i>" not in html


# --- Prognose ----------------------------------------------------------------

def _two(db, eintritt=date(2026, 10, 10)):
    a = create_member(db, "Anna Aktiv", "Aktiv", eintritt)
    p = create_member(db, "Paul Passiv", "Passiv", eintritt)
    return a, p


def test_forecast_default(client, db):
    a, p = _two(db)
    r = client.get("/forecast")
    assert r.status_code == 200
    t = _text(r.text)
    assert "Gesamt offen" in t and "27,00 €" in t
    assert "Noch fällig" in t and "Überfällig" in t
    assert "Anna Aktiv" in t and "2026-11" in t
    assert f'href="/members/{a}/history' in r.text


def test_forecast_one_paid(client, db):
    a, p = _two(db)
    tx = _tx(db, a, 1700, day="2026-11-02")
    confirm_allocations(db, tx, [("2026-11", 1700)])
    t = _text(client.get("/forecast").text)
    assert "10,00 €" in t and "27,00 €" not in t


def test_forecast_overdue_split(client, db):
    create_member(db, "Anna Aktiv", "Aktiv", date(2026, 8, 5))  # ab 2026-09
    t = _text(client.get("/forecast?until=2026-11-30").text)
    assert "34,00 €" in t and "17,00 €" in t and "51,00 €" in t


def test_forecast_quick_links(client, db):
    html = client.get("/forecast").text
    for u in ("2026-11-30", "2026-12-31", "2027-02-28"):
        assert f"until={u}" in html
    assert "Ende dieses Monats" in html and "In 3 Monaten (Monatsende)" in html


@pytest.mark.parametrize("until", ["abc", "2026-13-01", "1899-01-01", "2101-01-01",
                                   "9999-12-31", "2026-1-1"])
def test_forecast_invalid(client, until):
    r = client.get(f"/forecast?until={until}")
    assert r.status_code == 422 and "Datum" in r.text
    assert 'name="until"' in r.text


def test_forecast_empty(client, db):
    _erika(db)
    assert "Nichts offen" in client.get("/forecast?until=2026-01-31").text


# --- Wurzel, Sicherheit ------------------------------------------------------

def test_root_redirects_to_dues(client):
    r = client.get("/")
    assert r.status_code in (302, 303, 307) and r.headers["location"] == "/dues"


@pytest.mark.parametrize("path", ["/dues", "/forecast", "/members/1/history"])
def test_new_pages_headers_and_no_external(client, db, path):
    _erika(db)
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-security-policy"].startswith("default-src 'self'")
    assert re.search(r"(src|href|action)=[\"']https?://", r.text) is None
    assert "style=" not in r.text and "<script" not in r.text


@pytest.mark.parametrize("path", ["/dues", "/forecast", "/members/1/history"])
def test_new_pages_reject_bad_host(client, path):
    assert client.get(path, headers={"host": "evil.example"}).status_code == 400


# --- C1: Hinweis, solange der Prüfungsstart nicht festgelegt ist --------------

CHECK_NOTE = "Prüfungsstart ist noch nicht festgelegt – bitte vor dem ersten Import festlegen."


@pytest.mark.parametrize("path", ["/dues", "/import"])
def test_check_start_notice_until_set(client, db, path):
    html = client.get(path).text
    assert CHECK_NOTE in _text(html)
    assert 'href="/settings/fees"' in html.split("<main>")[1]
    set_check_start(db, "2026-01")
    assert CHECK_NOTE not in _text(client.get(path).text)


def test_settings_check_start_text_none(client):
    t = _text(client.get("/settings/fees").text)
    assert "Aktuell: nicht gesetzt." in t
    assert "abgeleitet" not in t


def test_settings_check_start_text_derived(client, db):
    _tx(db, None, 1700, day="2025-07-03")
    t = _text(client.get("/settings/fees").text)
    assert "Aktuell: nicht gesetzt (abgeleitet: 2025-07)." in t


def test_settings_check_start_text_set(client, db):
    _tx(db, None, 1700, day="2025-07-03")
    set_check_start(db, "2026-03")
    t = _text(client.get("/settings/fees").text)
    assert "Aktuell: 2026-03." in t and "nicht gesetzt" not in t
