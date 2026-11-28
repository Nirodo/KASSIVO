"""Journal-Prüfung der Wiederherstellung (H1): manipulierte Werte ändern nichts."""
import os
import json
import subprocess

import pytest

from kassivo import update_recover

TAG = "20261127-100000-ab12"


def _project(tmp_path):
    p = tmp_path / "projekt"
    (p / ".update" / f"a-{TAG}" / "src").mkdir(parents=True)
    (p / ".update" / f"a-{TAG}" / "src" / "x.py").write_text("alt")
    (p / "src").mkdir()
    (p / "src" / "x.py").write_text("neu")
    outside = tmp_path / "draussen"
    outside.mkdir()
    (outside / "wichtig.txt").write_text("bleibt")
    return p, outside


def _journal(p, **over):
    j = {"current": "1.2.0", "version": "1.3.0", "alt": f"a-{TAG}", "neu": f"n-{TAG}",
         "fehl": f"f-{TAG}", "files": [["src/x.py", True]], "pip_ran": False, "freeze": None}
    j.update(over)
    (p / ".update" / "journal.json").write_text(json.dumps(j))


def _state(root):
    return {q.relative_to(root).as_posix(): q.read_bytes()
            for q in sorted(root.rglob("*")) if q.is_file()}


def test_valid_journal_restores(tmp_path):
    p, _ = _project(tmp_path)
    _journal(p)
    update_recover.recover(p)
    assert (p / "src" / "x.py").read_text() == "alt"


@pytest.mark.parametrize("over", [
    {"alt": "../../draussen"}, {"alt": "a-x"}, {"neu": "/etc"}, {"fehl": "..\\draussen"},
    {"neu": f"a-{TAG}"}, {"alt": f"A-{TAG}"},
    {"freeze": "../../draussen/wichtig.txt"}, {"freeze": "pip-x.txt"},
    {"files": [["../draussen/wichtig.txt", True]]}, {"files": [["/etc/passwd", True]]},
    {"files": [["C:/x", True]]}, {"files": [["src\\..\\..\\x", True]]},
    {"files": [[".git/config", True]]}, {"files": [[".VENV/x", True]]},
    {"files": [[".update/journal.json", False]]}, {"files": [["src/./x.py", True]]},
    {"files": [["", True]]}, {"files": [["src/x.py", "ja"]]}, {"files": "src/x.py"},
    {"files": [["src/x.py"]]}, {"current": 1},
])
def test_manipulated_journal_changes_nothing(tmp_path, over):
    p, outside = _project(tmp_path)
    _journal(p, **over)
    before = _state(tmp_path)
    calls = []
    with pytest.raises(update_recover.JournalError):
        update_recover.recover(p, run=lambda *a, **k: calls.append(a))
    assert _state(tmp_path) == before
    assert calls == []


def test_symlinked_folder_out_of_project_is_refused(tmp_path):
    p, outside = _project(tmp_path)
    try:
        (p / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks nicht erlaubt")
    _journal(p, files=[["link/wichtig.txt", False]])
    with pytest.raises(update_recover.JournalError):
        update_recover.recover(p)
    assert (outside / "wichtig.txt").read_text() == "bleibt"


def test_main_reports_invalid_journal(tmp_path, capsys):
    p, _ = _project(tmp_path)
    _journal(p, alt="../x")
    assert update_recover.main([str(p)]) == 2
    assert "nichts geändert" in capsys.readouterr().out


def test_pip_timeout_during_restore_is_reported(tmp_path):
    p, _ = _project(tmp_path)
    (p / ".update" / f"pip-{TAG}.txt").write_text("fastapi==0.110.0\n")
    _journal(p, pip_ran=True, freeze=f"pip-{TAG}.txt")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("pip", 600)
    notes = update_recover.recover(p, run=slow)
    assert any("Reparatur" in n for n in notes)


@pytest.mark.parametrize("rel", [
    ".git./config", ".git /config", "GIT~1/config", ".venv./x", ".update./journal.json",
    ".env.", "docs./a.txt", "docs /a.txt", "src/datei.", "src/VENV~1/x",
])
def test_windows_aliases_of_protected_names_are_refused(tmp_path, rel):
    # Windows streicht Punkt und Leerzeichen am Ende und kennt 8.3-Kurznamen
    (tmp_path / ".git").mkdir()
    assert not update_recover.safe_rel(tmp_path, rel)


def test_link_into_protected_folder_is_refused(tmp_path):
    (tmp_path / ".git").mkdir()
    link = tmp_path / "harmlos"
    if os.name == "nt":
        # Junction braucht kein Symlink-Recht
        import _winapi
        _winapi.CreateJunction(str(tmp_path / ".git"), str(link))
    else:
        link.symlink_to(tmp_path / ".git", target_is_directory=True)
    assert not update_recover.safe_rel(tmp_path, "harmlos/config")
