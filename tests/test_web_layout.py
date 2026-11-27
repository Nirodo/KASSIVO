"""Basis-Layout (Issue #46): fünf Bereiche, Seitenleiste, Barrierefreiheit, volle Breite."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.helpers import make_app

AREAS = ["Übersicht", "Mitglieder", "Zahlungen", "Auswertung", "Einstellungen"]
CSS = Path(__file__).resolve().parents[1] / "src/kassivo/web/static/style.css"

# Seite -> (aktiver Bereich, Unterseite in der Seitenleiste)
PAGES = {
    "/": ("Übersicht", "/"),
    "/dues": ("Auswertung", "/dues"),
    "/forecast": ("Auswertung", "/forecast"),
    "/reports": ("Auswertung", "/reports"),
    "/members": ("Mitglieder", "/members"),
    "/members/new": ("Mitglieder", "/members"),
    "/transactions": ("Zahlungen", "/transactions"),
    "/unmatched": ("Zahlungen", "/unmatched"),
    "/auto": ("Zahlungen", "/auto"),
    "/import": ("Zahlungen", "/import"),
    "/settings/fees": ("Einstellungen", "/settings/fees"),
    "/categories": ("Einstellungen", "/categories"),
    "/backup": ("Einstellungen", "/backup"),
    "/wiki": ("Einstellungen", "/wiki"),
    "/wiki/start": ("Einstellungen", "/wiki"),
}


@pytest.fixture
def client(tmp_path):
    return TestClient(make_app(tmp_path / "t.db"), follow_redirects=False)


def _nav(html, label):
    m = re.search(rf'<nav[^>]*aria-label="{label}"[^>]*>.*?</nav>', html, re.S)
    assert m, label
    return m[0]


@pytest.mark.parametrize("path", PAGES)
def test_page_ok_and_skeleton(client, path):
    r = client.get(path)
    assert r.status_code == 200
    t = r.text
    assert '<html lang="de">' in t
    assert re.search(r'<a class="skip" href="#main">Zum Inhalt springen</a>', t)
    assert t.count("<main") == 1 and 'id="main"' in t
    assert "<script" not in t and "style=" not in t.replace("<style", "")


@pytest.mark.parametrize("path", PAGES)
def test_main_nav_has_five_areas_and_one_current(client, path):
    area, _ = PAGES[path]
    nav = _nav(client.get(path).text, "Hauptnavigation")
    links = re.findall(r"<a [^>]*>(.*?)</a>", nav, re.S)
    assert [l.strip() for l in links] == AREAS
    current = re.findall(r'<a [^>]*aria-current="page"[^>]*>(.*?)</a>', nav, re.S)
    assert [c.strip() for c in current] == [area]


@pytest.mark.parametrize("path", PAGES)
def test_sidebar_marks_current_subpage(client, path):
    _, sub = PAGES[path]
    html = client.get(path).text
    side = _nav(html, "Bereichsmenü [^\"]+[^ \"]")
    assert f'href="{sub}" aria-current="page"' in side
    assert side.count('aria-current="page"') == 1


def test_sidebar_groups_per_area(client):
    zahl = client.get("/transactions").text
    for href in ("/transactions", "/unmatched", "/auto", "/import"):
        assert f'href="{href}"' in zahl
    ausw = client.get("/dues").text
    for href in ("/dues", "/forecast", "/reports"):
        assert f'href="{href}"' in ausw
    einst = client.get("/backup").text
    for href in ("/settings/fees", "/categories", "/backup", "/wiki"):
        assert f'href="{href}"' in einst


def test_mobile_menu_is_details_without_js(client):
    t = client.get("/dues").text
    assert re.search(r'<details class="mnav">\s*<summary>Menü</summary>', t)
    assert "<script" not in t


def test_help_link_in_bar(client):
    assert 'href="/wiki"' in re.search(r"<header.*?</header>", client.get("/dues").text, re.S)[0]


def test_css_full_width_and_dark_mode():
    css = CSS.read_text("utf-8")
    # Seiteninhalt hat keine feste Maximalbreite
    for sel in ("main", ".page", ".content", ".layout"):
        for m in re.finditer(rf"(^|\n){re.escape(sel)}\s*\{{([^}}]*)\}}", css):
            assert "max-width:" not in m[2].replace("max-width: none", ""), sel
    assert "prefers-color-scheme: dark" in css
    assert "overflow-x: auto" in css
    assert "@import" not in css and "url(http" not in css


def test_sidebar_is_named_nav_without_trailing_space(client):
    t = client.get("/dues").text
    assert '<nav class="side" aria-label="Bereichsmenü Auswertung">' in t
    assert "<aside" not in t


def test_mobile_menu_has_help_link(client):
    panel = re.search(r'<div class="panel">.*?</div>', client.get("/members").text, re.S)[0]
    assert 'href="/wiki"' in panel


def test_unknown_page_renders_without_area(client):
    r = client.get("/gibt-es-nicht")
    assert r.status_code == 404
    assert 'class="side"' not in r.text and "layout-solo" in r.text
    nav = _nav(r.text, "Hauptnavigation")
    assert "aria-current" not in nav and nav.count("<a ") == 5


def test_uebersicht_sidebar_has_no_import_link(client):
    # /import gehört nur zu "Zahlungen"; die Seitenleiste der Übersicht hat keinen Behelfslink.
    nav_src = (Path(__file__).resolve().parents[1] / "src/kassivo/web/templates/_nav.html").read_text("utf-8")
    start = nav_src.index('("Übersicht"')
    block = nav_src[start:nav_src.index('("Mitglieder"')]
    assert '"/import"' not in block and '"/dues"' not in block


def test_every_get_route_belongs_to_an_area(client):
    """Jede HTML-Seite hat einen Bereich, ausser Weiterleitungen und Nicht-HTML."""
    # app.routes enthält neuere FastAPI-Router nur verschachtelt; das Schema listet alle Pfade flach.
    schema = client.app.openapi()["paths"]
    paths = sorted(p for p, ops in schema.items() if "get" in ops)
    assert "/dues" in paths and "/" in paths
    seen = 0
    for p in paths:
        url = re.sub(r"\{[^}]+\}", "1", p)
        r = client.get(url)
        if r.status_code in (301, 302, 303, 307, 308) or "text/html" not in r.headers.get("content-type", ""):
            continue
        nav = _nav(r.text, "Hauptnavigation")
        assert nav.count('aria-current="page"') == 1, p
        seen += 1
    assert seen >= 15
