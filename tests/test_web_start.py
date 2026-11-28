"""Startseite „Heute zu tun“ (Issue #48): Zähler gleich Zielseite, nur lesend. Nur erfundene Daten."""
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from kassivo import dues
from kassivo.db import connect
from kassivo.members import create_member
from kassivo.web.common import get_today
from tests.helpers import book_on, make_app

TODAY = date(2026, 11, 24)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "t.db"


@pytest.fixture
def app(db_path):
    app = make_app(db_path)
    app.dependency_overrides[get_today] = lambda: TODAY
    return app


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def db(db_path, app):
    c = connect(db_path)
    yield c
    c.close()


def _tx(db, member_id, cents, day, category=None, n=[0]):
    n[0] += 1
    db.execute("insert or ignore into account(id, iban) values(1, 'XX00FAKE')")
    cur = db.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, member_id,'
        " cp_name, purpose, dedupe_key) values(1,?,?,?,'Test Person','x',?)",
        (day, cents, member_id, f"s{n[0]}"))
    # nur vorhandene Konten (wie früher nur vorhandene Kategorien)
    if category and db.execute("select 1 from ledger_account where name=?",
                               (category,)).fetchone():
        book_on(db, cur.lastrowid, category)
    db.commit()
    return cur.lastrowid


@pytest.fixture
def filled(db):
    """Zwei Mitglieder, Prüfungsstart 2025-11: Rückstände in 2025 und 2026."""
    erika = create_member(db, "Erika Muster", "Aktiv", date(2025, 10, 1))
    max_ = create_member(db, "Max Muster", "Aktiv", date(2026, 8, 1))
    dues.set_check_start(db, "2025-11")
    _tx(db, erika, 1700, "2025-11-03")
    _tx(db, erika, 1700, "2026-11-03")
    _tx(db, max_, 2000, "2026-09-03")          # 20,00 bei 17,00: Vorschlag
    _tx(db, None, 999, "2026-05-01")           # unklar
    _tx(db, None, 500, "2026-06-01")           # unklar
    _tx(db, None, 321, "2025-06-01")           # unklar, Vorjahr
    _tx(db, None, 111, "2026-06-02", "Sonstige Einnahmen")  # kein Mitglied nötig
    _tx(db, None, -12000, "2026-04-06")        # Ausgabe
    dues.redistribute_all(db)
    return db


def _get(client, url):
    r = client.get(url)
    assert r.status_code == 200, url
    return r.text


def _count(html, key):
    found = re.findall(rf'data-count="{key}">(\d+)<', html)
    assert len(found) == 1, key
    return int(found[0])


def _href(html, key):
    m = re.search(rf'<a [^>]*href="([^"]+)"[^>]*>\s*<span [^>]*data-count="{key}"', html)
    return m[1].replace("&amp;", "&")


def _body_rows(html):
    return re.search(r"<tbody>(.*?)</tbody>", html, re.S)[1].count("<tr>")


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_root_renders_overview(client):
    t = _get(client, "/")
    assert re.search(r"<h1>\s*Übersicht\s*</h1>", t)
    assert re.search(r'<a class="button" href="/import">Kontoauszug importieren</a>', t)
    assert t.count('class="button"') == 1  # eine Hauptaktion


def test_unmatched_counter_matches_target(client, filled):
    start = _get(client, "/")
    assert _href(start, "unmatched") == "/unmatched"
    assert _count(start, "unmatched") == _body_rows(_get(client, "/unmatched")) == 3


def test_proposal_counter_matches_target(client, filled):
    start = _get(client, "/")
    url = _href(start, "proposals")
    assert url == "/transactions?year=all&proposed=1"
    target = _get(client, url)
    assert _count(start, "proposals") == _body_rows(target) == 1
    assert "Buchungen: <strong>1</strong>" in target


def test_auto_counter_matches_target(client, filled):
    start = _get(client, "/")
    assert _href(start, "auto") == "/auto"
    n = len(re.findall(r'action="/auto/\d+/undo"', _get(client, "/auto")))
    assert _count(start, "auto") == n == 2


def test_overdue_counter_per_year_matches_dues_grid(client, filled):
    start = _get(client, "/")
    for year in (2025, 2026):
        key = f"overdue-{year}"
        assert _href(start, key) == f"/dues?year={year}"
        cells = len(re.findall(r'<td class="status status-overdue',
                               _get(client, f"/dues?year={year}")))
        assert cells > 0
        assert _count(start, key) == cells
    assert 'data-count="overdue-2024"' not in start


def test_past_year_without_overdue_is_hidden_current_year_stays(client, db):
    m = create_member(db, "Erika Muster", "Aktiv", date(2026, 10, 1))  # zahlt ab November
    dues.set_check_start(db, "2025-11")
    _tx(db, m, 1700, "2026-11-03")
    dues.redistribute_all(db)
    start = _get(client, "/")
    assert _count(start, "overdue-2026") == 0
    assert "overdue-2025" not in start


def _overdue_cells(client, year):
    return len(re.findall(r'<td class="status status-overdue', _get(client, f"/dues?year={year}")))


def _overdue_years(html):
    return sorted(int(y) for y in re.findall(r'data-count="overdue-(\d+)"', html))


def test_without_check_start_only_notice_and_current_year(client, db):
    m = create_member(db, "Erika Muster", "Aktiv", date(2024, 10, 1))
    _tx(db, m, 1700, "2025-03-03")  # abgeleiteter Prüfungsstart 2025-03
    start = _get(client, "/")
    assert "Prüfungsstart ist noch nicht festgelegt" in start
    assert _overdue_years(start) == [2026]
    assert _count(start, "overdue-2026") == _overdue_cells(client, 2026) > 0


@pytest.fixture
def grid_years(monkeypatch):
    years, real = [], dues.dues_grid

    def spy(conn, year, today):
        years.append(year)
        return real(conn, year, today)

    monkeypatch.setattr(dues, "dues_grid", spy)
    return years


def test_very_old_check_start_begins_at_earliest_fee_start(db, grid_years):
    from kassivo.overview import overview
    create_member(db, "Erika Muster", "Aktiv", date(2025, 10, 1))  # zahlt ab 2025-11
    create_member(db, "Max Muster", "Aktiv", date(2026, 8, 1))
    dues.set_check_start(db, "1900-01")
    o = overview(db, TODAY)
    assert grid_years == [2025, 2026]
    assert [y["year"] for y in o["overdue"]] == [2025, 2026]


def test_very_old_check_start_counts_still_match_dues(client, db):
    create_member(db, "Erika Muster", "Aktiv", date(2025, 10, 1))
    dues.set_check_start(db, "1900-01")
    start = _get(client, "/")
    assert _overdue_years(start) == [2025, 2026]
    for year in (2025, 2026):
        assert _count(start, f"overdue-{year}") == _overdue_cells(client, year) > 0
    assert _overdue_cells(client, 2024) == 0


def test_member_without_fee_start_keeps_check_start(db, grid_years):
    from kassivo.overview import overview
    create_member(db, "Erika Muster", "Aktiv", date(2025, 10, 1))
    other = create_member(db, "Max Muster", "Aktiv", date(2026, 8, 1))
    db.execute("update member set fee_start=null where id=?", (other,))
    db.commit()
    dues.set_check_start(db, "2023-01")
    overview(db, TODAY)
    assert grid_years == [2023, 2024, 2025, 2026]


def test_new_year_shows_december_of_previous_year(app, client, db):
    app.dependency_overrides[get_today] = lambda: date(2027, 1, 1)
    m = create_member(db, "Erika Muster", "Aktiv", date(2026, 10, 1))  # zahlt ab 2026-11
    dues.set_check_start(db, "2026-11")
    _tx(db, m, 1700, "2026-11-03")
    dues.redistribute_all(db)
    start = _get(client, "/")
    assert "Beiträge Januar 2027" in start and "Beitragsjahr 2027" in start
    assert _overdue_years(start) == [2026, 2027]
    assert _count(start, "overdue-2026") == _overdue_cells(client, 2026) == 1
    assert _count(start, "overdue-2027") == _overdue_cells(client, 2027) == 0
    assert _count(start, "month-open") == 1
    assert 'href="/reports?year=2027"' in start


def test_check_start_in_the_future(client, db):
    create_member(db, "Erika Muster", "Aktiv", date(2025, 10, 1))
    dues.set_check_start(db, "2027-03")
    start = _get(client, "/")
    assert _overdue_years(start) == [2026]
    assert _count(start, "overdue-2026") == _overdue_cells(client, 2026) == 0
    assert "Für diesen Monat ist kein Beitrag fällig." in start
    assert "Zahlungen verteilt" not in start


def test_member_who_left(client, db):
    from kassivo.members import leave_member
    m = create_member(db, "Erika Muster", "Aktiv", date(2026, 1, 15))  # zahlt ab 2026-02
    dues.set_check_start(db, "2026-01")
    leave_member(db, m, date(2026, 4, 30))
    start = _get(client, "/")
    assert _count(start, "overdue-2026") == _overdue_cells(client, 2026) == 3
    assert "Für diesen Monat ist kein Beitrag fällig." in start
    assert "Zahlungen verteilt" not in start


def test_paid_without_expected_is_explained(client, db):
    from kassivo.members import leave_member
    m = create_member(db, "Erika Muster", "Aktiv", date(2026, 1, 15))
    dues.set_check_start(db, "2026-01")
    leave_member(db, m, date(2026, 4, 30))
    tx = _tx(db, m, 1700, "2026-11-03")
    dues.confirm_allocations(db, tx, [("2026-11", 1700)])
    text = _text(_get(client, "/"))
    assert "Bezahlt 17,00 € von 0,00 € erwartet" in text
    assert ("Auf diesen Monat sind Zahlungen verteilt, obwohl kein Beitrag fällig ist"
            " (zum Beispiel nach einem Austritt).") in text


def test_unmatched_text(client, filled):
    assert ("Zahlungseingänge ohne Mitglied (ohne Kategorie oder mit „Beiträge“), alle Jahre"
            in _get(client, "/"))


def test_month_status_matches_dues_grid(client, filled):
    start = _get(client, "/")
    assert "November 2026" in start
    grid = _get(client, "/dues?year=2026")
    rows = re.findall(r'<tr><th scope="row"><a .*?</tr>', grid, re.S)
    cells = [re.findall(r'<td class="status status-(\w+)', r)[10] for r in rows]
    assert _count(start, "month-due") == sum(c != "none" for c in cells) == 2
    assert _count(start, "month-paid") == cells.count("paid") == 1
    assert _count(start, "month-open") == cells.count("open") == 1
    assert _count(start, "month-partial") == cells.count("partial") == 0
    foot = re.search(r"<tfoot>.*?</tfoot>", grid, re.S)[0]
    paid, expected = re.findall(r'<td class="num">(.*?)</td>', foot)[10].split(" / ")
    assert f"Bezahlt {paid} € von {expected} erwartet" in _text(start)


def test_year_figures_match_report(client, filled):
    start, report = _get(client, "/"), _get(client, "/reports?year=2026")
    for label in ("Einnahmen", "Ausgaben", "Saldo"):
        value = re.search(rf"<dt>{label}</dt><dd>(.*?)</dd>", report)[1]
        assert re.search(rf"<dt>{label}</dt><dd>{re.escape(value)}</dd>", start), label
    # 17,00 + 20,00 + 9,99 + 5,00 + 1,11 = 53,10 Einnahmen, 120,00 Ausgaben
    assert "<dt>Saldo</dt><dd>-66,90 €</dd>" in start
    assert 'href="/reports?year=2026"' in start
    assert "nicht der Kontostand" in start


def test_empty_database_shows_first_steps(client):
    t = _get(client, "/")
    assert "Noch keine Daten" in t
    for href in ("/settings/fees", "/members/new", "/import", "/wiki/start"):
        assert f'href="{href}"' in t
    assert "data-count=" not in t


def test_members_without_transactions_is_not_empty(client, db):
    create_member(db, "Erika Muster", "Aktiv", date(2026, 1, 15))
    t = _get(client, "/")
    assert "Noch keine Daten" not in t and "Heute zu tun" in t


def test_check_start_notice(client, db):
    note = "Prüfungsstart ist noch nicht festgelegt"
    assert note in _get(client, "/")
    create_member(db, "Erika Muster", "Aktiv", date(2026, 1, 15))
    assert note in _get(client, "/")
    dues.set_check_start(db, "2026-02")
    assert note not in _get(client, "/")


def test_no_month_contribution_due(client, db):
    create_member(db, "Erika Muster", "Aktiv", date(2026, 11, 15))  # zahlt ab Dezember
    assert "Für diesen Monat ist kein Beitrag fällig." in _get(client, "/")


@pytest.mark.parametrize("with_data", [False, True])
def test_get_root_writes_nothing(client, db_path, request, with_data):
    if with_data:
        request.getfixturevalue("filled")
    watch = connect(db_path)
    try:
        def state():
            return watch.execute("pragma data_version").fetchone()[0], list(watch.iterdump())

        before = state()
        _get(client, "/")
        assert state() == before
    finally:
        watch.close()


def test_overview_uses_only_reading_statements(filled):
    from kassivo.overview import overview
    before = filled.total_changes
    overview(filled, TODAY)
    assert filled.total_changes == before and not filled.in_transaction


def test_only_get(client):
    assert client.post("/").status_code == 405


def test_headers_no_inline_and_bad_host(client, filled):
    r = client.get("/")
    assert r.headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
    assert "<script" not in r.text and "style=" not in r.text
    assert re.search(r"(src|href|action)=[\"']https?://", r.text) is None
    assert client.get("/", headers={"host": "evil.example"}).status_code == 400


def test_sidebar_has_only_overview(client):
    t = _get(client, "/")
    side = re.search(r'<nav class="side" aria-label="Bereichsmenü Übersicht">.*?</nav>', t, re.S)[0]
    assert re.findall(r'href="([^"]+)"', side) == ["/"]
    assert "Beitragsraster (Auswertung)" not in t
