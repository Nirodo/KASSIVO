"""Die Versionsnummer steht in pyproject.toml und im Paket und muss übereinstimmen."""
import re
from pathlib import Path

import kassivo

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match is not None
    assert kassivo.__version__ == match.group(1)


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", kassivo.__version__)
