"""Weboberfläche: Jahresreport mit Diagrammen. Nur erfundene Daten."""
import hashlib
import json
import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kassivo.db import connect
from kassivo.reports import year_summary
from tests.helpers import make_app
from kassivo.web.common import get_today

TODAY = date(2026, 11, 24)
STATIC = Path(__file__).parent.parent / "src" / "kassivo" / "web" / "static"
HEADERS = ("content-security-policy", "x-content-type-options", "referrer-policy")


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


def _tx(db, day, amount, category=None, n=[0]):
    n[0] += 1
    db.execute("insert or ignore into account(id, iban) values(1, 'DE00TEST')")
    cat = None
    if category:
        db.execute("insert or ignore into category(name, kind) values(?, 'both')", (category,))
        cat = db.execute("select id from category where name=?", (category,)).fetchone()[0]
    db.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, category_id,'
        " dedupe_key) values(1,?,?,?,?)", (day, amount, cat, f"k{n[0]}"))
    db.commit()


def _fill(db):
    _tx(db, "2026-03-05", 1700, "Beiträge")
    _tx(db, "2026-04-10", -3000, "Miete")
    _tx(db, "2026-05-10", -2000, "Material")


def test_report_headline_and_structure(client, db):
    _fill(db)
    r = client.get("/reports?year=2026")
    assert r.status_code == 200
    h = r.text
    assert "17,00 €" in h and "50,00 €" in h and "-33,00 €" in h
    assert len(re.findall(r"<canvas[^>]*role=\"img\"[^>]*aria-label=\"[^\"]+\"", h)) == 3
    assert h.count("<canvas") == 3
    assert h.count("<table") == 3 and h.count("<caption>") == 3
    assert "<th scope=\"col\">" in h
    for name in ("Beiträge", "Miete", "Material"):
        assert name in h
    assert 'data-year="2026"' in h
    assert 'href="/reports?year=2025"' in h and 'href="/reports?year=2027"' in h


def test_report_default_year_is_today(client, db):
    _fill(db)
    assert "17,00 €" in client.get("/reports").text


def test_report_empty_year(client, db):
    r = client.get("/reports?year=2020")
    assert r.status_code == 200
    assert "Keine Buchungen in diesem Jahr." in r.text
    assert "<canvas" not in r.text


def test_report_scripts_local_only(client, db):
    _fill(db)
    h = client.get("/reports?year=2026").text
    assert re.findall(r"<script\b[^>]*>", h) == [
        '<script src="/static/vendor/chart.umd.min.js">',
        '<script src="/static/reports.js">',
    ]
    assert not re.search(r"<script(?![^>]*\bsrc=)", h)
    assert "http://" not in h and "https://" not in h
    assert "style=" not in h


def test_year_nav_limits(client):
    h = client.get("/reports?year=1900").text
    assert 'year=1899' not in h and 'year=1901' in h
    h = client.get("/reports?year=2100").text
    assert 'year=2101' not in h and 'year=2099' in h


@pytest.mark.parametrize("y", ["1899", "abc", "2101", "", "%3Cb%3E", "20 26"])
def test_invalid_year(client, y):
    r = client.get(f"/reports?year={y}")
    assert r.status_code == 422
    assert "text/html" in r.headers["content-type"]
    assert "Jahr" in r.text
    r = client.get(f"/reports/data?year={y}")
    assert r.status_code == 422
    assert "application/json" in r.headers["content-type"]
    assert "error" in r.json()


def test_data_equals_summary(client, db):
    _fill(db)
    r = client.get("/reports/data?year=2026")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {**year_summary(db, 2026), "year": 2026}
    assert len(r.json()["by_month"]) == 12
    assert r.json()["income_cents"] == 1700


def test_data_default_year(client, db):
    assert client.get("/reports/data").json()["year"] == 2026


def test_xss_name_escaped(client, db):
    evil = "<script>alert(1)</script>"
    _tx(db, "2026-03-05", 1700, evil)
    h = client.get("/reports?year=2026").text
    assert evil not in h
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in h
    r = client.get("/reports/data?year=2026")
    assert r.json()["by_category"][0]["name"] == evil


def test_bad_host(client):
    assert client.get("/reports", headers={"host": "evil.example"}).status_code == 400


@pytest.mark.parametrize("path", ["/reports", "/reports/data", "/reports?year=abc",
                                  "/static/reports.js", "/static/vendor/chart.umd.min.js"])
def test_security_headers(client, path):
    r = client.get(path)
    for h in HEADERS:
        assert h in r.headers


def test_static_served(client):
    r = client.get("/static/vendor/chart.umd.min.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    r = client.get("/static/reports.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]


def test_vendor_integrity():
    data = (STATIC / "vendor" / "chart.umd.min.js").read_bytes()
    readme = (STATIC / "vendor" / "README.md").read_text(encoding="utf-8")
    assert hashlib.sha256(data).hexdigest() in readme
    assert "4.4.1" in readme and "MIT" in readme
    text = data.decode("utf-8")
    assert len(data) > 150_000
    assert text.startswith("!function(") and "Chart" in text[:200]
    assert "4.4.1" in text


def test_vendor_license_file():
    lic = STATIC / "vendor" / "LICENSE.chartjs.txt"
    text = lic.read_text(encoding="utf-8")
    assert "MIT License" in text and "Chart.js Contributors" in text
    assert "LICENSE.chartjs.txt" in (STATIC / "vendor" / "README.md").read_text(encoding="utf-8")


def test_reports_js_safe():
    js = (STATIC / "reports.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "outerHTML" not in js
    assert "insertAdjacentHTML" not in js and "document.write" not in js
    assert "eval(" not in js and "new Function" not in js
    assert "topSlices" in js and "formatEuro" in js and "data-year" in js or "dataset.year" in js
