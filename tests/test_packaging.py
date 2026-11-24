"""Paketierung: Templates und statische Dateien gehören ins Wheel."""
import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
PKG = ROOT / "src" / "kassivo"


def _package_data() -> list[str]:
    text = (ROOT / "pyproject.toml").read_text("utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10: kleiner Parser ohne Abhängigkeit
        m = re.search(r"^\[tool\.setuptools\.package-data\]\s*\n(.*?)(?=^\[|\Z)",
                      text, re.S | re.M)
        assert m, "package-data fehlt"
        k = re.search(r"^kassivo\s*=\s*\[(.*?)\]", m[1], re.S | re.M)
        assert k, "package-data für kassivo fehlt"
        return re.findall(r'"([^"]+)"', k[1])
    return tomllib.loads(text)["tool"]["setuptools"]["package-data"]["kassivo"]


def _covered(rel: str, patterns: list[str]) -> bool:
    """Wie setuptools: Muster gelten je Ordner, "*" geht nicht in Unterordner."""
    folder, _, name = rel.rpartition("/")
    return any(pat.rpartition("/")[0] == folder
               and fnmatch.fnmatchcase(name, pat.rpartition("/")[2]) for pat in patterns)


def test_every_template_and_static_file_is_package_data():
    patterns = _package_data()
    files = [p.relative_to(PKG).as_posix()
             for d in ("web/templates", "web/static") for p in (PKG / d).rglob("*")
             if p.is_file() and "__pycache__" not in p.parts]
    assert "web/templates/base.html" in files
    assert [f for f in files if not _covered(f, patterns)] == []


def test_fallback_parser_matches_tomllib(monkeypatch):
    expected = _package_data()
    import sys
    monkeypatch.setitem(sys.modules, "tomllib", None)  # wie Python 3.10
    assert _package_data() == expected
