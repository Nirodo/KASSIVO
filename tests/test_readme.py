"""README: Pflichtabschnitte, Befehle und Zahlen gegen den echten Code. Nur erfundene Daten."""
import re
from pathlib import Path

import pytest

from kassivo import ingest
from kassivo.settings import default_db_path
from kassivo.web import import_routes, transactions_routes

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "kassivo"
README = (ROOT / "README.md").read_text(encoding="utf-8")

SECTIONS = [
    "Überblick", "Funktionen im Detail", "Voraussetzungen", "Installation", "Start",
    "Konfiguration", "Empfohlener Ablauf", "Import-Format und Ablauf", "Regeln im Überblick",
    "Berichte", "Backup und Wiederherstellung", "Datenschutz und Sicherheit", "Tests",
    "Projektstruktur", "Architektur in Kürze", "Entwicklung", "Bekannte Einschränkungen",
    "Drittanbieter", "Lizenz",
]


def test_tagline_stays_on_top():
    lines = README.splitlines()
    assert lines[0] == "# KASSIVO"
    assert lines[1].startswith("KASSIVO ist die moderne Lösung zur einfachen und übersichtlichen")


@pytest.mark.parametrize("name", SECTIONS)
def test_section_exists(name):
    assert re.search(rf"^## {re.escape(name)}\s*$", README, re.M), name


def test_sections_in_order():
    pos = [re.search(rf"^## {re.escape(n)}\s*$", README, re.M).start() for n in SECTIONS]
    assert pos == sorted(pos)


@pytest.mark.parametrize("needle", [
    "python -m kassivo", "http://127.0.0.1:8000", "127.0.0.1", "KASSIVO_DB", "CAMT", "Backup",
    "/wiki", "feat/", "fix/", "Chart.js", "4.4.1", "MIT", "src/kassivo/web/static/vendor/LICENSE.chartjs.txt",
    "pip install -e", "pip install .", "venv", "PowerShell", "SemVer", "Pull Request", "Issue",
    "%LOCALAPPDATA%", "OneDrive", "Content-Security-Policy", "Host", "Origin", "keine Anmeldung",
    "pytest", "SQLite", "Cent", "Wiki",
])
def test_mentions(needle):
    assert needle in README, needle


def test_wiki_topics_linked_by_name():
    from kassivo.web.wiki_routes import TOPICS
    for slug, (title, *_rest) in TOPICS.items():
        assert title in README, f"Wiki-Thema „{title}“ fehlt im README"


def test_no_hard_coded_test_count_and_no_agent_setup():
    assert not re.search(r"\b\d{3,} Tests\b", README)
    for word in ("Subagent", "kassivo-architect", "kassivo-builder", "Modellwahl", "Claude"):
        assert word not in README, word


def test_no_real_data_or_bank_account_fragments():
    assert not re.search(r"\b[A-Z]{2}\d{2}(?: ?\d{4}){3,}", README)  # IBAN-Muster
    for fake in re.findall(r"XX00[ A-Z0-9]+", README):
        assert "FAKE" in fake


def test_limits_in_readme_match_code():
    mb = lambda n: f"{n // (1024 * 1024)} MB"  # noqa: E731
    assert mb(import_routes.MAX_UPLOAD_BYTES) in README
    assert mb(ingest.MAX_XML_BYTES) in README
    assert mb(ingest.MAX_TOTAL_BYTES) in README
    assert str(ingest.MAX_FILES) in README
    assert f"{transactions_routes.PAGE_SIZE} Buchungen" in README


def test_default_paths_in_readme_match_code():
    assert str(default_db_path({}, "win32", "C:/Users/x")).endswith("KASSIVO\\kassivo.db") or \
        str(default_db_path({}, "win32", "C:/Users/x")).endswith("KASSIVO/kassivo.db")
    assert r"%LOCALAPPDATA%\KASSIVO\kassivo.db" in README
    assert "~/.local/share/kassivo/kassivo.db" in README
    assert "$XDG_DATA_HOME/kassivo/kassivo.db" in README


def test_project_tree_lists_every_module_and_template():
    for p in SRC.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".css", ".js"):
            assert p.name in README or p.name == "__init__.py", f"{p.name} fehlt in der Projektstruktur"
    for folder in ("templates", "wiki", "static", "vendor", "tests", "docs"):
        assert folder in README


def test_wiki_and_startup_facts():
    assert "Datenbank:" in README and "Sicherungen:" in README
    assert "Port 8000" in README or "8000" in README
    assert re.search(r"Wiederherstellung \(von Hand\)", README)
    assert README.count("Wiederherstellung (von Hand)") == 1  # test_backup teilt daran


def test_readme_links_resolve():
    for target in re.findall(r"\]\(([^)#]+)\)", README):
        if target.startswith(("http://", "https://")):
            continue
        assert (ROOT / target).exists(), target


@pytest.mark.parametrize("needle", [
    "Eingabeaufforderung (cmd)", "set KASSIVO_DB=", "$env:KASSIVO_DB", "setx KASSIVO_DB",
    "Entwicklungsstand vor 1.0.0", "`chore:`", "Uvicorn-Zugriffsprotokoll", "Bankreferenz",
])
def test_readme_documents_shell_variants_and_limits(needle):
    assert needle in README, needle


def test_readme_mentions_category_clears_unmatched():
    assert "Kein Mitglied nötig" in README


def test_readme_documents_import_robustness_facts():
    for fact in ("Einträge ohne Status", "UTF-8, US-ASCII und ISO-8859-1", "bereits bekannter Bankreferenz",
                 "Sparkasse-Tagesdateien tragen Bankreferenzen"):
        assert fact in README, fact
