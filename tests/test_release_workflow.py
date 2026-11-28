"""release.yml: baut bei Tag vX.Y.Z Archiv, Wheel und SHA256SUMS. Textprüfungen ohne YAML-Paket."""
import re
from pathlib import Path

from kassivo import update

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"
TEXT = (WF / "release.yml").read_text("utf-8") if (WF / "release.yml").exists() else ""
USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.M)


def test_release_workflow_exists_and_runs_on_version_tags_only():
    assert TEXT
    on = re.search(r"^on:\n(.*?)^\S", TEXT, re.S | re.M)[1]
    assert "tags:" in on and '"v[0-9]+.[0-9]+.[0-9]+"' in on
    assert "pull_request" not in on and "branches" not in on


def test_actions_are_pinned_like_tests_yml():
    tests_yml = (WF / "tests.yml").read_text("utf-8")
    pinned = set(USES.findall(tests_yml))
    used = USES.findall(TEXT)
    assert used
    for ref in used:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
        assert ref in pinned, f"{ref} nicht wie in tests.yml gepinnt"


def test_permissions_minimal():
    top = re.search(r"^permissions:\n((?:  .*\n)+)", TEXT, re.M)[1]
    assert top.strip() == "contents: read"
    assert TEXT.count("contents: write") == 1
    assert "persist-credentials: false" in TEXT


def test_no_expression_injection_in_run_steps():
    """Werte aus dem Tag nur über env, nie direkt per ${{ }} im Skript."""
    for block in re.findall(r"run: \|\n((?:\s{10,}.*\n?)+)", TEXT):
        assert "${{" not in block, block


def test_builds_archive_wheel_and_checksums_without_overwrite():
    assert 'git archive --format=zip --prefix="kassivo-${VERSION}/"' in TEXT
    assert "pip wheel --no-deps" in TEXT
    assert "sha256sum" in TEXT and "SHA256SUMS" in TEXT
    assert "--clobber" not in TEXT
    assert "docs/releases/" in TEXT


def test_checks_tag_against_version_and_runs_tests_first():
    assert "pyproject.toml" in TEXT and "__version__" in TEXT
    assert "python -m pytest" in TEXT
    assert re.search(r"needs:\s*test", TEXT)


def test_asset_names_match_what_the_app_expects():
    assert "kassivo-${VERSION}.zip" in TEXT
    assert update.DOWNLOAD_PREFIX.startswith("https://github.com/Nirodo/KASSIVO/")
