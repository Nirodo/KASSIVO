"""Weboberfläche: Jahresreport mit Diagrammen. Nur erfundene Daten."""
import hashlib
import json
import re
import shutil
import subprocess
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
        '<script src="/static/reports-lib.js">',
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
                                  "/static/reports.js", "/static/reports-lib.js",
                                  "/static/vendor/chart.umd.min.js"])
def test_security_headers(client, path):
    r = client.get(path)
    for h in HEADERS:
        assert h in r.headers


def test_static_served(client):
    r = client.get("/static/vendor/chart.umd.min.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    for name in ("reports.js", "reports-lib.js"):
        r = client.get("/static/" + name)
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


def test_by_member_caption_names_contribution_months(client, db):
    _tx(db, "2026-03-05", 1700)
    html = client.get("/reports?year=2026").text
    assert ("<caption>Beiträge für die Beitragsmonate 2026 (nach Beitragsmonat, "
            "nicht nach Buchungsdatum)</caption>") in html


def test_vendor_license_file():
    lic = STATIC / "vendor" / "LICENSE.chartjs.txt"
    text = lic.read_text(encoding="utf-8")
    assert "MIT License" in text and "Chart.js Contributors" in text
    assert "LICENSE.chartjs.txt" in (STATIC / "vendor" / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["reports.js", "reports-lib.js"])
def test_reports_js_safe(name):
    js = (STATIC / name).read_text(encoding="utf-8")
    assert "innerHTML" not in js and "outerHTML" not in js
    assert "insertAdjacentHTML" not in js and "document.write" not in js
    assert "eval(" not in js and "new Function" not in js
    assert "http://" not in js and "https://" not in js


def test_reports_js_uses_lib_and_year():
    js = (STATIC / "reports.js").read_text(encoding="utf-8")
    assert "topSlices" in js and "formatEuro" in js
    assert "data-year" in js or "dataset.year" in js
    assert "ReportsLib" in js and "s.other" in js and "i === MAX_SLICES" not in js
    assert "getDataVisibility" in js and "console.error" in js
    lib = (STATIC / "reports-lib.js").read_text(encoding="utf-8")
    assert "other: true" in lib


def test_empty_notes_and_separate_errors_prepared(client, db):
    _tx(db, "2026-03-05", -3000, "Miete")
    h = client.get("/reports?year=2026").text
    assert "Keine Einnahmen in diesem Jahr." in h and "Keine Ausgaben in diesem Jahr." in h
    assert "Die Daten konnten nicht geladen werden." in h
    assert "Das Diagramm konnte nicht gezeichnet werden." in h
    assert 'id="chart-income-box"' in h and 'id="chart-expense-box"' in h


def test_data_income_only_year_has_no_expense(client, db):
    _tx(db, "2026-03-05", 1700, "Beiträge")
    d = client.get("/reports/data?year=2026").json()
    assert d["by_category"] and all(c["expense_cents"] == 0 for c in d["by_category"])


def test_data_expense_only_year_has_no_income(client, db):
    _tx(db, "2026-03-05", -3000, "Miete")
    d = client.get("/reports/data?year=2026").json()
    assert d["by_category"] and all(c["income_cents"] == 0 for c in d["by_category"])


@pytest.mark.parametrize("amount", [1700, -3000])
def test_notes_and_errors_hidden_initially(client, db, amount):
    _tx(db, "2026-03-05", amount, "X")
    h = client.get("/reports?year=2026").text
    for i in ("chart-income-empty", "chart-expense-empty", "chart-error-load", "chart-error-draw"):
        m = re.search(rf'<p id="{i}" class="([^"]*)" role="status"', h)
        assert m and "hidden" in m.group(1).split(), i


def test_by_member_explanation(client, db):
    _tx(db, "2026-03-05", 1700)
    h = client.get("/reports?year=2026").text
    assert "Die Beiträge je Mitglied zählen nach Beitragsmonat" in h
    assert "Einnahmen oben nach Buchungsdatum" in h
    assert h.index("Die Beiträge je Mitglied zählen") > h.index("<h3>Beiträge je Mitglied</h3>")
    assert h.index("Die Beiträge je Mitglied zählen") < h.index("<caption>Beiträge für")


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="Node.js nicht installiert: JavaScript-Tests übersprungen")
def test_js_unit_tests():
    js_dir = Path(__file__).parent / "js"
    files = sorted(js_dir.glob("*.test.mjs"))
    assert files, "keine JavaScript-Testdateien gefunden"
    r = subprocess.run(["node", "--test", *map(str, files)], capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
