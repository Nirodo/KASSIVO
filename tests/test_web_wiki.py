"""Wiki im Programm: Routen, Navigation, Hilfe-Links, Sicherheit. Nur erfundene Daten."""
import html
import re
from datetime import date
from pathlib import Path

import pytest

from kassivo.db import connect
from kassivo.members import create_member
from kassivo.web import wiki_routes
from kassivo.web.wiki_routes import GROUPS, TOPICS
from tests.helpers import make_client

TEMPLATES = Path(__file__).parent.parent / "src" / "kassivo" / "web" / "templates"
HREF = re.compile(r'href="([^"]*)"')
SLUGS = list(TOPICS)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "t.db"


@pytest.fixture
def client(db_path):
    return make_client(db_path)


def _hrefs(text: str) -> list[str]:
    return [html.unescape(h) for h in HREF.findall(text)]


def _all_pages(client) -> dict[str, str]:
    pages = {"/wiki": client.get("/wiki").text}
    for slug in SLUGS:
        pages[f"/wiki/{slug}"] = client.get(f"/wiki/{slug}").text
    return pages


# --- Whitelist ------------------------------------------------------------------

def test_whitelist_shape():
    for slug, (title, template, summary, group) in TOPICS.items():
        assert re.fullmatch(r"[a-z]+", slug)
        assert title and summary
        assert group in GROUPS
        assert template == f"wiki/{slug}.html"
        assert (TEMPLATES / template).is_file()


def test_groups_are_known():
    assert {t[3] for t in TOPICS.values()} <= set(GROUPS)


# --- Seiten ---------------------------------------------------------------------

@pytest.mark.parametrize("slug", SLUGS)
def test_topic_page_renders(client, slug):
    r = client.get(f"/wiki/{slug}")
    assert r.status_code == 200
    title = TOPICS[slug][0]
    h1 = re.search(r"<h1>(.*?)</h1>", r.text, re.S)
    assert h1 and title in html.unescape(h1[1])
    assert "Wofür ist das?" in r.text and "Siehe auch" in r.text


def test_index_links_every_topic_with_summary(client):
    r = client.get("/wiki")
    assert r.status_code == 200
    assert "<h1>" in r.text and "Schnellstart" in r.text
    links = _hrefs(r.text)
    for slug, (title, _, summary, group) in TOPICS.items():
        assert f"/wiki/{slug}" in links
        assert html.escape(summary, quote=False) in r.text or summary in html.unescape(r.text)


@pytest.mark.parametrize("path", ["/wiki"] + [f"/wiki/{s}" for s in SLUGS])
def test_sidebar_links_all_topics(client, path):
    text = client.get(path).text
    nav = re.search(r'<nav class="wiki-toc".*?</nav>', text, re.S)
    assert nav, "Seitenleiste fehlt"
    links = _hrefs(nav[0])
    for slug in SLUGS:
        assert f"/wiki/{slug}" in links
    assert "/wiki" in links


def test_prev_next_links(client):
    for i, slug in enumerate(SLUGS):
        text = client.get(f"/wiki/{slug}").text
        nav = re.search(r'<nav class="wiki-pager".*?</nav>', text, re.S)
        assert nav
        links = _hrefs(nav[0])
        assert "/wiki" in links  # zurück zur Übersicht
        expected = set()
        if i > 0:
            expected.add(f"/wiki/{SLUGS[i - 1]}")
        if i + 1 < len(SLUGS):
            expected.add(f"/wiki/{SLUGS[i + 1]}")
        assert {l for l in links if l.startswith("/wiki/")} == expected


KNOWN_PAGES = {"/", "/dues", "/members", "/members/new", "/transactions", "/unmatched",
               "/categories", "/forecast", "/reports", "/import", "/backup", "/settings/fees",
               "/auto", "/settings/update", "/zuordnungen"}


def test_no_dead_internal_links(client):
    seen = set()
    for page, text in _all_pages(client).items():
        for href in _hrefs(text):
            if href in ("#wiki-inhalt", "#main"):  # Sprungmarken zum Inhalt, Ziele werden eigens geprüft
                continue
            if href.startswith(("http:", "https:", "mailto:", "#")):
                pytest.fail(f"externer oder Anker-Link auf {page}: {href}")
            if not href.startswith("/") or href.startswith("/static/"):
                continue
            path = href.split("#")[0].split("?")[0]
            if path in seen:
                continue
            seen.add(path)
            if path.startswith("/wiki"):
                assert client.get(path).status_code == 200, f"{page} -> {href}"
            else:
                assert path in KNOWN_PAGES, f"{page} verweist auf unbekannte Seite {href}"
                assert client.get(href).status_code != 404, f"{page} -> {href}"


def test_wiki_pages_link_only_without_ids(client):
    for page, text in _all_pages(client).items():
        for href in _hrefs(text):
            assert not re.search(r"/(members|transactions)/\d", href), f"{page}: {href}"


# --- Unbekannte Slugs -----------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/wiki/unbekannt", "/wiki/START", "/wiki/" + "a" * 200, "/wiki/%2e%2e", "/wiki/..%2fbase",
    "/wiki/base", "/wiki/_wiki_base", "/wiki/index", "/wiki/start.html", "/wiki/wiki%2fstart",
    "/wiki/%00", "/wiki/start%00",
])
def test_unknown_slug_is_german_404(client, path):
    r = client.get(path)
    assert r.status_code == 404
    assert "Seite nicht gefunden." in r.text


def test_trailing_slash_is_not_a_topic_page(client):
    r = client.get("/wiki/start/")
    assert r.status_code in (307, 308, 404)
    if r.status_code != 404:
        assert r.headers["location"].endswith("/wiki/start")


def test_slug_lookup_uses_whitelist_only(client, monkeypatch):
    """Eine Vorlage wird nie aus der Anfrage gebaut: nur Einträge der Tabelle zählen."""
    monkeypatch.setitem(TOPICS, "neu", ("Neu", "wiki/start.html", "x", GROUPS[0]))
    assert client.get("/wiki/neu").status_code == 200  # Tabelle entscheidet
    monkeypatch.delitem(TOPICS, "neu")
    assert client.get("/wiki/neu").status_code == 404
    src = Path(wiki_routes.__file__).read_text("utf-8")
    assert "f\"wiki/" not in src and "f'wiki/" not in src and '"wiki/" +' not in src


# --- Nur lesend, Sicherheit -----------------------------------------------------------

@pytest.mark.parametrize("path", ["/wiki", "/wiki/start"])
def test_wiki_is_get_only(client, path):
    for method in ("post", "put", "delete", "patch"):
        r = getattr(client, method)(path)
        assert r.status_code == 405, (method, path)


def test_wiki_has_security_headers_and_origin_check(client):
    r = client.get("/wiki/start")
    assert r.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    r = client.post("/wiki", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_wiki_rejects_foreign_host(client):
    for path in ("/wiki", "/wiki/start"):
        assert client.get(path, headers={"Host": "evil.example"}).status_code == 400


def test_wiki_changes_no_state(client, db_path):
    client.get("/wiki")
    for slug in SLUGS:
        client.get(f"/wiki/{slug}")
    conn = connect(db_path)
    try:
        assert conn.execute("select count(*) from member").fetchone()[0] == 0
        assert conn.execute('select count(*) from "transaction"').fetchone()[0] == 0
    finally:
        conn.close()


# --- Inhalt: CSP, keine Reste ---------------------------------------------------------

def test_no_external_urls_inline_code_or_leftovers(client):
    for page, text in _all_pages(client).items():
        assert not re.search(r"https?://(?!127\.0\.0\.1|localhost)", text), page
        assert not re.search(r'(?:href|src|action)="//', text), page
        assert "<script" not in text and "<style" not in text, page
        assert not re.search(r"\sstyle=", text), page
        assert not re.search(r"\son[a-z]+=", text), page
        assert "{{" not in text and "{%" not in text and "{#" not in text, page
        for word in ("TODO", "TBD", "XXX", "FIXME", "Lorem"):
            assert word not in text, (page, word)


def test_wiki_css_present():
    css = (TEMPLATES.parent / "static" / "style.css").read_text("utf-8")
    for cls in (".wiki", ".wiki-toc", ".callout", ".callout-tip", ".callout-warn",
                ".callout-example", ".steps", ".help-link", ".wiki-pager"):
        assert cls in css, cls


# --- Navigation und Hilfe-Links -------------------------------------------------------

def test_nav_has_wiki_link(client):
    text = client.get("/dues").text
    bar = re.search(r"<header.*?</header>", text, re.S)[0]
    assert 'href="/wiki"' in bar and 'aria-label="Hilfe (Wiki)"' in bar


@pytest.fixture
def seeded(db_path, client):
    conn = connect(db_path)
    mid = create_member(conn, "Max Mustermann", "Aktiv", date(2026, 1, 10),
                        iban="XX00 FAKE 0000 0000 01")
    conn.execute("insert into account(iban, label) values('XX00 FAKE 0000 0000 99', 't')")
    cur = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key,'
        " cp_iban, member_id) values(1, '2026-03-05', 1700, 'k1', 'XX00FAKE0000000001', ?)",
        (mid,))
    conn.commit()
    ids = {"member": mid, "tx": cur.lastrowid}
    conn.close()
    return ids


HELP_PAGES = {
    "/members": "mitglieder",
    "/members/new": "mitglieder",
    "/settings/fees": "beitraege",
    "/import": "import",
    "/transactions": "zuordnung",
    "/unmatched": "zuordnung",
    "/categories": "kategorien",
    "/dues": "beitragsuebersicht",
    "/forecast": "prognose",
    "/reports": "reports",
    "/backup": "backup",
    "/auto": "verteilung",
}


@pytest.mark.parametrize("path,slug", sorted(HELP_PAGES.items()))
def test_page_has_help_link(client, path, slug):
    text = client.get(path).text
    assert f'<a href="/wiki/{slug}">Hilfe zu dieser Seite</a>' in text
    assert 'class="help-link"' in text


@pytest.mark.parametrize("template,slug", [
    ("/members/{member}", "mitglieder"),
    ("/members/{member}/history", "beitragsuebersicht"),
    ("/transactions/{tx}/allocate", "verteilung"),
])
def test_id_pages_have_help_link(client, seeded, template, slug):
    path = template.format(**seeded)
    r = client.get(path)
    assert r.status_code == 200
    assert f'<a href="/wiki/{slug}">Hilfe zu dieser Seite</a>' in r.text


def test_help_link_comes_right_after_heading(client):
    text = client.get("/backup").text
    assert text.index("</h1>") < text.index('class="help-link"') < text.index("<section>")


def test_help_link_macro_accepts_custom_text():
    from kassivo.web.common import templates
    t = templates.env.from_string(
        '{% from "_macros.html" import help_link %}{{ help_link("start", "Mehr") }}')
    out = t.render()
    assert '<a href="/wiki/start">Mehr</a>' in out


def test_packaging_covers_wiki_folder():
    text = (TEMPLATES.parents[3] / "pyproject.toml").read_text("utf-8")
    assert "web/templates/wiki/*.html" in text


@pytest.mark.parametrize("path", ["/wiki"] + [f"/wiki/{s}" for s in SLUGS])
def test_sidebar_has_no_headings_skip_link_and_current(client, path):
    text = client.get(path).text
    nav = re.search(r'<nav class="wiki-toc".*?</nav>', text, re.S)[0]
    assert "<h2" not in nav and 'class="toc-group"' in nav
    assert text.index('href="#wiki-inhalt"') < text.index('<nav class="wiki-toc"')
    assert "Zum Inhalt springen" in text and 'id="wiki-inhalt"' in text
    assert nav.count('aria-current="page"') == 1
    if path != "/wiki":
        assert f'<a href="{path}" aria-current="page">' in nav
