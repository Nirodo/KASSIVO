"""Wiki-Inhalt: Fakten, Beschriftungen und Zahlen gegen den echten Code geprüft.

Verhindert, dass die Hilfeseiten still vom Programm abweichen. Nur erfundene Daten.
"""
import html
import re
from datetime import date
from pathlib import Path

import pytest

from kassivo import dues, ingest
from kassivo.backup import MAX_LISTED
from kassivo.db import connect
from kassivo.fees import fee_at
from kassivo.forecast import forecast
from kassivo.members import create_member, expected_cents, leave_member, set_member_type
from kassivo.money import format_eur
from kassivo.months import add_months, month_of
from kassivo.web import categories_routes, dues_routes, import_routes, settings_routes, transactions_routes
from kassivo.web.app import SECURITY_HEADERS
from kassivo.web.wiki_routes import GROUPS, TOPICS
from tests.helpers import make_client

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "kassivo"
TEMPLATE_TEXT = html.unescape("\n".join(
    p.read_text("utf-8") for p in (SRC / "web" / "templates").rglob("*.html")
    if "wiki" not in p.parts))
# Zeichenketten, die der Code über mehrere Zeilen aneinanderfügt, wieder zusammensetzen
PY_TEXT = re.sub(r'"\s*\n\s*"', "", "\n".join(p.read_text("utf-8") for p in SRC.rglob("*.py")))

EXPECTED_SLUGS = ["start", "begriffe", "mitglieder", "beitraege", "import", "zuordnung",
                  "verteilung", "kategorien", "beitragsuebersicht", "prognose", "reports",
                  "backup", "sicherheit", "faq", "grenzen"]


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    client = make_client(tmp_path_factory.mktemp("wiki") / "t.db")
    return {s: html.unescape(client.get(f"/wiki/{s}").text) for s in TOPICS}


def _text(page: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", page))


def test_all_topics_present_in_order():
    assert list(TOPICS) == EXPECTED_SLUGS
    assert {t[3] for t in TOPICS.values()} == set(GROUPS)


def test_index_shows_every_group(tmp_path):
    text = html.unescape(make_client(tmp_path / "t.db").get("/wiki").text)
    for group in GROUPS:
        assert f"<h3>{group}</h3>" in text


def test_help_links_in_templates_point_to_existing_topics():
    slugs = set(re.findall(r'help_link\("([a-z]+)"', TEMPLATE_TEXT))
    assert len(slugs) >= 8
    assert slugs <= set(TOPICS)


def test_every_page_has_standard_sections(pages):
    for slug, page in pages.items():
        for heading in ("Wofür ist das?", "So geht's", "Gut zu wissen", "Siehe auch"):
            assert f"<h2>{heading}</h2>" in page, (slug, heading)
        if slug not in ("begriffe", "sicherheit", "faq", "grenzen"):
            assert "<h2>Beispiele</h2>" in page, slug


def test_faq_has_at_least_ten_questions(pages):
    assert len(re.findall(r"<h3>\d+\. ", pages["faq"])) >= 10


@pytest.mark.parametrize("slug,words", [
    ("verteilung", ["Buchungsmonat", "34,00", "17,00", "Vorschlag", "bestätigt"]),
    ("import", ["camt.052.001.08", "Duplikat", "Es wurde nichts importiert"]),
    ("zuordnung", ["mehrdeutig", "Absender merken", "ausgetreten", "Unklar"]),
    ("backup", ["journal", "kassivo.db", "backups"]),
    ("backup", ["automatisch eine Sicherung", "neueren Version", "kassivo-"]),
    ("grenzen", ["Datenbankformat"]),
    # Hinweise zu Nebenwirkungen und Grenzen (geschlossene Dokumentationslücken)
    ("beitraege", ["Vorschlag", "Prüfungsstart", "Verteilung aufheben", "1.500"]),
    ("start", ["Offene Vorschläge", "$env:KASSIVO_DB", "setx", "Eingabeaufforderung (cmd)"]),
    ("zuordnung", ["Verteilung aufheben", "frühesten", "— keins —", "nie für das richtige Mitglied"]),
    ("faq", ["mehrdeutig", "— keins —", "Bankreferenz"]),
    ("import", ["Bankreferenz", "später", "Regeln anwenden", "Unklare automatisch zuordnen", "für immer"]),
    ("grenzen", ["Bankreferenz", "für immer", "Barzahlungen", "mehrdeutig", "Längenbegrenzung",
                 "Monat bereits bezahlt", "Austritt vor dem Eintritt"]),
    ("begriffe", ["Buchungsmonat", "Beitragsmonat", "Soll"]),
    ("prognose", ["ganze Monat des Datums"]),
    ("verteilung", ["Gibt es keinen, geht er in den Buchungsmonat"]),
    ("mitglieder", ["Überfällig"]),
    ("beitragsuebersicht", ["Überzahlungen"]),
    ("kategorien", ["17.00"]),
    ("sicherheit", ["/transactions?q=", "Konsolenausgabe"]),
    ("grenzen", ["FinTS", "0,00", "C24", "finAPI"]),
    ("beitraege", ["Prüfungsstart", "Gültig ab Monat", "neu berechnet"]),
    ("kategorien", ["Priorität", "Ohne Kategorie", "Überschreiben", "Hauptverein"]),
    ("sicherheit", ["127.0.0.1", "keine Anmeldung", "Ungültiger Host", "OneDrive"]),
    ("start", ["python -m kassivo", "KASSIVO_DB", "Prüfungsstart", "Absender merken"]),
])
def test_fact_words(pages, slug, words):
    for w in words:
        assert w in _text(pages[slug]), (slug, w)


def test_callouts_have_text_labels(pages):
    labels = {"callout-tip": "Tipp:", "callout-warn": "Achtung:", "callout-example": "Beispiel:"}
    found = set()
    for page, text in pages.items():
        for cls, label in labels.items():
            for m in re.finditer(rf'<div class="callout {cls}">(.*?)</div>', text, re.S):
                found.add(cls)
                assert label in m[1], (page, cls)
    assert found == set(labels)


# --- zitierte Beschriftungen gibt es in den Vorlagen -----------------------------------

LABELS = {
    "mitglieder": ["Mitglied anlegen", "Anlegen", "Zahlt ab Monat", "Art ändern",
                   "Austritt eintragen", "Neue Art", "Gültig ab Monat", "Austrittsdatum",
                   "Name suchen", "Suchen", "Speichern", "Bearbeiten", "Mitgliedsart ändern",
                   "Eintritt"],
    "beitraege": ["Beitrag ändern", "Betrag in Euro", "Beitrag speichern", "Verlauf der Beiträge",
                  "Prüfungsstart (Monat)", "Prüfungsstart speichern",
                  "Prüfungsstart ist noch nicht festgelegt", "Gültig ab Monat"],
    "import": ["Datei (ZIP oder XML)", "Importieren", "Import abgeschlossen",
               "Unklare Buchungen ansehen", "XML CAMT V8, gebuchte Umsätze", "Duplikate",
               "Automatisch kategorisiert", "Automatisch zugeordnet"],
    "zuordnung": ["Unklare automatisch zuordnen", "Absender merken", "Zuordnen",
                  "Mitglied zuordnen", "— keins —", "Verteilung aufheben",
                  "Ich bestätige, dass die Verteilung aufgehoben wird", "Offene Vorschläge",
                  "(ausgetreten)", "Weiter", "Zurück"],
    "verteilung": ["Verteilung bestätigen", "Neu vorschlagen", "Verteilung aufheben",
                   "Verteilung bearbeiten", "Aktuelle Verteilung", "Beitragsstatus des Mitglieds",
                   "Monat bereits bezahlt", "Summe der Zeilen", "Die Summe passt."],
    "kategorien": ["Kategorie anlegen", "Regel anlegen", "Regeln anwenden",
                   "Gesetzte Kategorien überschreiben",
                   "Ich bestätige das Überschreiben (nur nötig beim Überschreiben)", "Setzen",
                   "Filtern", "Ohne Kategorie", "Verwendungszweck", "Name der Gegenpartei",
                   "Enthält (Pflicht)", "Mindestbetrag", "Höchstbetrag", "Priorität",
                   "Regeln auf alle Buchungen anwenden", "Regeln angewendet", "Name (Pflicht)"],
    "beitragsuebersicht": ["Summe (bezahlt / erwartet)", "Bezahlt", "Teilweise", "Offen",
                           "Überfällig", "Vorschlag", "Stammdaten bearbeiten",
                           "Zur Beitragsübersicht", "Keine Mitglieder vorhanden."],
    "prognose": ["Offene Beiträge bis einschließlich (JJJJ-MM-TT)", "Anzeigen", "Schnellwahl",
                 "Ende dieses Monats", "Ende nächsten Monats", "Ende des Jahres",
                 "In 3 Monaten (Monatsende)", "Gesamt offen", "Noch fällig",
                 "Offene Beiträge im Einzelnen", "Nichts offen bis zu diesem Datum."],
    "reports": ["Ausgaben nach Kategorie", "Einnahmen nach Kategorie",
                "Einnahmen und Ausgaben je Monat", "Zahlen im Detail",
                "Keine Buchungen in diesem Jahr.", "Einnahmen und Ausgaben je Kategorie",
                "Einnahmen, Ausgaben und Saldo je Monat", "Beiträge für die Beitragsmonate",
                "Die Diagramme konnten nicht geladen werden. "
                "Die Tabellen unten enthalten dieselben Zahlen."],
    "backup": ["Sicherung erstellen", "Sicherung erstellt:", "Vorhandene Sicherungen"],
    "start": ["Prüfungsstart speichern", "Mitglied anlegen", "Beitrag speichern", "Importieren",
              "Absender merken", "Zuordnen", "Offene Vorschläge", "Verteilung bestätigen",
              "Prüfungsstart (Monat)"],
}


@pytest.mark.parametrize("slug", sorted(LABELS))
def test_quoted_labels_exist_in_templates(pages, slug):
    for label in LABELS[slug]:
        assert label in _text(pages[slug]), f"{slug}: Wiki nennt „{label}“ nicht mehr"
        assert label in TEMPLATE_TEXT or label in PY_TEXT, f"„{label}“ fehlt in Vorlagen und Code"


# Meldungen, die das Wiki wörtlich zitiert: auf der Seite und im Code (oder in Vorlagen).
MESSAGES = [
    ("zuordnung", "hat bestätigte Verteilung: Verteilung zuerst aufheben"),
    ("zuordnung", "Zugeordnet. Die Absender-IBAN gehört mehreren Mitgliedern und wird künftig "
                  "nicht automatisch zugeordnet."),
    ("beitraege", "Prüfungsstart gespeichert."),
    ("import", "Es wurde nichts importiert."),
    ("import", "Bitte eine Datei auswählen."),
    ("import", "Dateityp nicht erkannt. Erwartet wird eine ZIP- oder XML-Datei."),
    ("import", "Keine XML-Dateien gefunden"),
    ("import", "Import insgesamt zu groß"),
    ("import", "Mehrere Berichte in einer Datei werden nicht unterstützt."),
    ("import", "kein camt.052.001.08-Dokument"),
    ("import", "DOCTYPE/ENTITY nicht erlaubt"),
    ("import", "ungültiger Betrag"),
    ("import", "Betrag oder Soll/Haben-Kennzeichen fehlt"),
    ("import", "Saldo stimmt nicht"),
    ("kategorien", "Zum Überschreiben bitte ausdrücklich bestätigen."),
    ("prognose", "Das Datum liegt zu weit in der Zukunft (höchstens 50 Jahre)."),
    ("sicherheit", "Ungültiger Host."),
    ("sicherheit", "Anfrage von fremder Herkunft abgelehnt."),
]


@pytest.mark.parametrize("slug,message", MESSAGES)
def test_quoted_messages_exist_in_code(pages, slug, message):
    assert message in _text(pages[slug]), (slug, message)
    assert message in PY_TEXT or message in TEMPLATE_TEXT, message


# --- Zahlen aus dem Code -----------------------------------------------------------------

def _mb(n: int) -> str:
    return f"{n // (1024 * 1024)} MB"


def test_import_limits_match_code(pages):
    t = _text(pages["import"])
    assert f"höchstens {_mb(import_routes.MAX_UPLOAD_BYTES)} groß" in t
    assert f"Grenze {_mb(import_routes.MAX_UPLOAD_BYTES)}" in t
    assert f"höchstens {ingest.MAX_FILES} XML-Dateien" in t
    assert f"höchstens {_mb(ingest.MAX_XML_BYTES)} groß" in t
    assert f"höchstens {_mb(ingest.MAX_TOTAL_BYTES)}" in t
    assert f"größer als {_mb(ingest.MAX_TOTAL_BYTES)}" in t
    assert f"größer als {_mb(ingest.MAX_XML_BYTES)}" in t
    assert f"(> {ingest.MAX_FILES})" in t
    assert f"Grenze {_mb(import_routes.MAX_UPLOAD_BYTES)}" in PY_TEXT


def test_forecast_horizon_is_fifty_years(pages):
    conn = connect(":memory:")
    today = date(2026, 3, 15)
    limit = add_months(month_of(today), 600)
    forecast(conn, date(int(limit[:4]), int(limit[5:]), 1), today)  # genau 50 Jahre geht
    nxt = add_months(limit, 1)
    with pytest.raises(ValueError):
        forecast(conn, date(int(nxt[:4]), int(nxt[5:]), 1), today)
    assert "höchstens 50 Jahre" in dues_routes.E_HORIZON
    assert "höchstens 50 Jahre" in _text(pages["prognose"])


def test_other_numbers_match_code(pages):
    assert f"{transactions_routes.PAGE_SIZE} Buchungen je Seite" in _text(pages["zuordnung"])
    assert format_eur(settings_routes.MAX_FEE_CENTS) == "100.000,00 €"
    assert "100.000,00 €" in _text(pages["beitraege"])
    assert f"0 bis {categories_routes.MAX_PRIORITY}" in _text(pages["kategorien"])
    assert f"zeigt die {MAX_LISTED} neuesten" in _text(pages["backup"])
    assert f"höchstens {dues.MAX_MONTHS_AHEAD} Monate" in _text(pages["verteilung"])
    assert categories_routes.MAX_NAME == 100 and "1 bis 100 Zeichen" in _text(pages["kategorien"])


def test_default_fees_and_port_match_code(pages):
    conn = connect(":memory:")
    assert fee_at(conn, "Aktiv", "2026-01") == 1700 and fee_at(conn, "Passiv", "2026-01") == 1000
    t = _text(pages["start"])
    assert "Aktiv" in t and "17,00 €" in t and "Passiv" in t and "10,00 €" in t
    main = (SRC / "__main__.py").read_text("utf-8")
    assert "port=8000" in main and 'host="127.0.0.1"' in main
    assert "8000" in _text(pages["sicherheit"]) and "8000" in _text(pages["faq"])
    cats = [r["name"] for r in conn.execute("select name from category order by id")]
    assert cats == ["Beiträge", "Hauptverein", "Sonstiges"]
    for c in cats:
        assert c in t


def test_security_claims_match_app(pages):
    assert "default-src 'self'" in SECURITY_HEADERS["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in SECURITY_HEADERS["Content-Security-Policy"]
    assert "nur auf <code>127.0.0.1</code>" in pages["sicherheit"]


# --- Rechenbeispiele des Wikis laufen so, wie dort beschrieben --------------------------

def _setup():
    conn = connect(":memory:")
    conn.execute("insert into account(iban, label) values('XX00FAKE0000000099', 't')")
    dues.set_check_start(conn, "2026-01")
    mid = create_member(conn, "Max Mustermann", "Aktiv", date(2025, 12, 10))
    return conn, mid


def _pay(conn, mid, day, cents):
    cur = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key, member_id)'
        " values(1, ?, ?, ?, ?)", (day, cents, f"{day}-{cents}", mid))
    conn.commit()
    return cur.lastrowid


def _rows(conn, tx):
    return [tuple(r) for r in conn.execute(
        "select month, cents, status from allocation where tx_id=? order by month", (tx,))]


@pytest.mark.parametrize("day,cents,expected", [
    ("2026-01-15", 1700, [("2026-01", 1700, "confirmed")]),
    ("2026-01-15", 3400, [("2026-01", 1700, "proposed"), ("2026-02", 1700, "proposed")]),
    ("2026-01-15", 1000, [("2026-01", 1000, "proposed")]),
    ("2026-01-15", 2000, [("2026-01", 1700, "proposed"), ("2026-02", 300, "proposed")]),
])
def test_distribution_examples(day, cents, expected):
    conn, mid = _setup()
    tx = _pay(conn, mid, day, cents)
    dues.propose_allocations(conn, tx)
    assert _rows(conn, tx) == expected


def test_distribution_rest_after_partial_payment():
    conn, mid = _setup()
    first = _pay(conn, mid, "2026-01-15", 1000)
    dues.propose_allocations(conn, first)
    dues.confirm_allocations(conn, first, [("2026-01", 1000)])
    rest = _pay(conn, mid, "2026-02-10", 700)
    dues.propose_allocations(conn, rest)
    assert _rows(conn, rest) == [("2026-01", 700, "proposed")]
    assert dues.month_status(conn, mid, "2026-01", date(2026, 1, 20)).status == "PARTIAL"
    assert dues.month_status(conn, mid, "2026-01", date(2026, 2, 1)).status == "OVERDUE"


def test_distribution_payment_at_month_end():
    conn, mid = _setup()
    first = _pay(conn, mid, "2026-01-03", 1700)
    for m in range(1, 13):
        conn.execute("insert into allocation(tx_id, member_id, month, cents, status)"
                     " values(?,?,?,?,'confirmed')", (first, mid, f"2026-{m:02d}", 1700))
    conn.commit()
    tx = _pay(conn, mid, "2026-12-30", 1700)
    dues.propose_allocations(conn, tx)
    assert _rows(conn, tx) == [("2027-01", 1700, "proposed")]


def test_member_start_and_leave_examples():
    conn, mid = _setup()
    for eintritt in (date(2026, 1, 10), date(2026, 1, 31)):
        m = create_member(conn, "Erika Musterfrau", "Aktiv", eintritt)
        assert conn.execute("select fee_start from member where id=?", (m,)).fetchone()[0] == "2026-02"
    leave_member(conn, mid, date(2026, 3, 5))
    assert dues.month_status(conn, mid, "2026-03", date(2026, 12, 1)).status == "OVERDUE"
    assert dues.month_status(conn, mid, "2026-04", date(2026, 12, 1)).status == "NONE"
    set_member_type(conn, m, "Passiv", "2026-07")
    assert expected_cents(conn, m, "2026-06") == 1700 and expected_cents(conn, m, "2026-07") == 1000


def test_forecast_example():
    conn, mid = _setup()
    tx = _pay(conn, mid, "2026-01-10", 1700)
    dues.confirm_allocations(conn, tx, [("2026-01", 1700)])
    today = date(2026, 3, 15)
    f = forecast(conn, date(2026, 3, 31), today)
    assert (f.overdue_cents, f.due_cents) == (1700, 1700)
    f = forecast(conn, date(2026, 6, 30), today)
    assert (f.overdue_cents, f.due_cents) == (1700, 6800)


# --- Dokumentierte Nebenwirkungen und Grenzen laufen so, wie das Wiki sie beschreibt ------

def test_parse_cents_matches_wiki_amount_format():
    from kassivo.money import parse_cents
    assert parse_cents("1.500") == 150000 and parse_cents("17,00") == 1700
    with pytest.raises(ValueError):
        parse_cents("17.00")


def test_payment_before_check_start_is_proposed_for_start_month():
    conn = connect(":memory:")
    conn.execute("insert into account(iban, label) values('XX00FAKE0000000099', 't')")
    dues.set_check_start(conn, "2026-04")
    mid = create_member(conn, "Max Mustermann", "Aktiv", date(2025, 12, 10))
    tx = _pay(conn, mid, "2026-03-20", 1700)
    dues.propose_allocations(conn, tx)
    assert _rows(conn, tx) == [("2026-04", 1700, "proposed")]
    dues.confirm_allocations(conn, tx, [("2026-04", 1700)])
    april = _pay(conn, mid, "2026-04-10", 1700)
    dues.propose_allocations(conn, april)
    assert _rows(conn, april) == [("2026-05", 1700, "proposed")]


def test_sibling_reassignment_flow():
    from kassivo import matching
    conn, erika = _setup()
    hans = create_member(conn, "Hans Musterfrau", "Aktiv", date(2025, 12, 10))
    first = _pay(conn, erika, "2026-01-10", 1700)
    dues.propose_allocations(conn, first)
    tx = _pay(conn, None, "2026-02-05", 1700)
    conn.execute('update "transaction" set cp_iban=? where id=?', ("XX00FAKE0000000002", tx))
    matching.assign_member(conn, tx, erika)
    assert _rows(conn, tx) == [("2026-02", 1700, "confirmed")]
    with pytest.raises(ValueError, match="hat bestätigte Verteilung: Verteilung zuerst aufheben"):
        matching.assign_member(conn, tx, hans)
    dues.clear_allocations(conn, tx)
    matching.assign_member(conn, tx, hans)
    assert _rows(conn, tx) == [("2026-01", 1700, "proposed")]  # frühester Fehlbetrag
    assert matching.iban_is_ambiguous(conn, "XX00FAKE0000000002")


def test_keins_is_reassigned_by_auto_assign_and_ambiguity_is_permanent():
    from kassivo import matching
    conn, mid = _setup()
    other = create_member(conn, "Erika Musterfrau", "Aktiv", date(2025, 12, 10))
    iban = "XX00FAKE0000000002"
    tx = _pay(conn, None, "2026-01-05", 1700)
    conn.execute('update "transaction" set cp_iban=? where id=?', (iban, tx))
    matching.assign_member(conn, tx, mid)
    dues.clear_allocations(conn, tx)
    matching.assign_member(conn, tx, None)
    assert matching.auto_assign(conn, [tx]) == 1  # "— keins —" hält nicht
    dues.clear_allocations(conn, tx)
    matching.assign_member(conn, tx, None)
    matching.assign_member(conn, tx, other)  # mit "Absender merken": jetzt mehrdeutig
    assert matching.iban_is_ambiguous(conn, iban)
    dues.clear_allocations(conn, tx)
    matching.assign_member(conn, tx, None)
    matching.assign_member(conn, tx, other)  # nie für das richtige Mitglied neu gelernt
    assert matching.iban_is_ambiguous(conn, iban)


def test_dedupe_limits_described_in_wiki(tmp_path):
    from tests.helpers import IBAN, make_camt
    conn = connect(":memory:")

    def imp(name, entries, day):
        f = tmp_path / name
        f.write_bytes(make_camt(IBAN, 0, entries, day))
        return ingest.import_path(conn, f)

    imp("a.xml", [{"amount": 1700, "ref": "REF9", "purpose": "A"}], "2026-03-05")
    r = imp("b.xml", [{"amount": 2500, "ref": "REF9", "purpose": "B"}], "2026-03-06")
    assert (r.new, r.duplicates) == (0, 1)  # gleiche Bankreferenz, anderer Betrag
    imp("c.xml", [{"amount": 1700, "ref": "", "purpose": "Z"}], "2026-04-02")
    r = imp("d.xml", [{"amount": 1700, "ref": "", "purpose": "Z"}], "2026-04-02")
    assert (r.new, r.duplicates) == (0, 1)  # laufende Nummer je Datei neu
    r = imp("e.xml", [{"amount": 1700, "ref": "", "purpose": "Y"}] * 2, "2026-05-02")
    assert (r.new, r.duplicates) == (2, 0)  # in derselben Datei bleiben beide


def test_zero_amount_message_is_quoted_exactly(tmp_path):
    from kassivo.camt import CamtError
    from tests.helpers import IBAN, make_camt
    f = tmp_path / "a.xml"
    f.write_bytes(make_camt(IBAN, 0, [{"amount_raw": "0.00", "amount": 1, "ref": "R0"}], "2026-03-05"))
    with pytest.raises(CamtError, match="a.xml: ungültiger Betrag"):
        ingest.import_path(connect(":memory:"), f)


def test_dues_footer_counts_paid_in_dash_cells():
    conn, mid = _setup()
    leave_member(conn, mid, date(2026, 2, 10))
    tx = _pay(conn, mid, "2026-04-01", 1700)
    conn.execute("insert into allocation(tx_id, member_id, month, cents, status)"
                 " values(?,?,?,?,'confirmed')", (tx, mid, "2026-04", 1700))
    conn.commit()
    st = dues.month_status(conn, mid, "2026-04", date(2026, 6, 1))
    assert (st.status, st.expected, st.paid) == ("NONE", 0, 1700)


def test_forecast_counts_whole_month_of_date():
    conn, mid = _setup()
    f = forecast(conn, date(2026, 3, 10), date(2026, 3, 15))
    assert f.overdue_cents + f.due_cents == 3 * 1700  # Januar bis März


def test_glossary_links_resolve(pages):
    ids = set(re.findall(r'<dt id="([a-z]+)">', pages["begriffe"]))
    assert {"buchungsmonat", "beitragsmonat", "soll"} <= ids
    for page in pages.values():
        for frag in re.findall(r'href="/wiki/begriffe#([a-z]+)"', page):
            assert frag in ids, frag
    for slug in ("verteilung", "mitglieder", "beitragsuebersicht"):
        assert "/wiki/begriffe#soll" in pages[slug], slug


@pytest.mark.parametrize("slug,words", [
    ("zuordnung", ["Zinsen für Guthaben", "Kein Mitglied nötig", "Als erledigt kategorisieren"]),
    ("kategorien", ["Zinsen für Guthaben", "Als erledigt kategorisieren"]),
    ("begriffe", ["Kein Mitglied nötig"]),
    ("faq", ["Zinsen", "Kein Mitglied nötig"]),
])
def test_unmatched_category_docs_issue_28(pages, slug, words):
    text = html.unescape(re.sub(r"<[^>]+>", " ", pages[slug]))
    for w in words:
        assert w in text, (slug, w)
