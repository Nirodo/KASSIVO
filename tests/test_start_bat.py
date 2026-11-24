"""start.bat: Startskript für Windows. Struktur und Dokumentation absichern."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAT = ROOT / "start.bat"


def _text() -> str:
    return BAT.read_bytes().decode("ascii")  # nur ASCII: keine Codepage-Probleme


def test_start_bat_exists_and_is_ascii():
    assert BAT.is_file()
    _text()


def test_start_bat_runs_the_app_from_the_venv():
    text = _text()
    assert r".venv\Scripts\python.exe" in text
    assert "-m kassivo" in text
    assert 'cd /d "%~dp0"' in text  # funktioniert aus jedem Ordner und per Doppelklick


def test_start_bat_opens_the_local_address_only():
    text = _text()
    assert "http://127.0.0.1:8000" in text
    assert "localhost" not in text.lower()


def test_start_bat_has_nobrowser_switch_and_missing_venv_hint():
    text = _text()
    assert "nobrowser" in text
    assert "virtuelle Umgebung" in text
    assert "pip install" in text


def test_gitattributes_forces_crlf_for_bat():
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat text eol=crlf" in attrs


def test_readme_and_wiki_mention_start_bat():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    wiki = (ROOT / "src/kassivo/web/templates/wiki/start.html").read_text(encoding="utf-8")
    assert "start.bat" in readme
    assert "start.bat" in wiki
