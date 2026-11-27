"""Update installieren (update.bat): Sicherung, Austausch, Rückweg. Nur erfundene Daten."""
import hashlib
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from kassivo import update, update_recover
from tests.update_helpers import make_archive, sha

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 27, 10, 0, 0)


def _snapshot(project: Path) -> dict:
    return {p.relative_to(project).as_posix(): p.read_bytes()
            for p in sorted(project.rglob("*"))
            if p.is_file() and ".update" not in p.relative_to(project).parts}


@pytest.fixture
def env(tmp_path):
    project = tmp_path / "projekt"
    (project / "src" / "kassivo").mkdir(parents=True)
    (project / "src" / "kassivo" / "__init__.py").write_text('__version__ = "1.2.0"\n')
    (project / "src" / "kassivo" / "alt_modul.py").write_text("x = 1\n")
    (project / "pyproject.toml").write_text(
        '[project]\nname = "kassivo"\nversion = "1.2.0"\ndependencies = ["fastapi>=0.110"]\n')
    (project / "README.md").write_text("alt\n")
    (project / ".venv").mkdir()
    (project / ".venv" / "marker").write_text("venv")
    (project / ".git").mkdir()
    (project / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (project / "eigene-notiz.txt").write_text("bleibt")

    data = tmp_path / "daten"
    data.mkdir()
    db = data / "kassivo.db"
    conn = sqlite3.connect(db)
    conn.execute("create table member(id integer primary key, name text)")
    conn.execute("insert into member(name) values('Max Mustermann')")
    conn.commit()
    conn.close()

    updates = data / "updates"
    updates.mkdir()
    archive = make_archive("1.3.0", {"kassivo-1.3.0/src/kassivo/neu_modul.py": "y = 2\n"})
    zip_path = updates / "kassivo-1.3.0.zip"
    zip_path.write_bytes(archive)
    (updates / "kassivo-1.3.0.zip.sha256").write_text(sha(archive) + "\n")
    return {"project": project, "db": db, "zip": zip_path, "updates": updates}


class FakeRun:
    def __init__(self, version="1.3.0", fail=()):
        self.calls, self.version, self.fail = [], version, set(fail)

    def __call__(self, args, **kw):
        self.calls.append((list(args), kw))
        if "freeze" in args:
            kind = "freeze"
        elif "-r" in args:
            kind = "restore"
        elif "pip" in args:
            kind = "pip"
        else:
            kind = "smoke"
        if kind in self.fail:
            return subprocess.CompletedProcess(args, 1, "", "Fehler")
        if kind == "interrupt":
            raise KeyboardInterrupt
        if kind + "-interrupt" in self.fail:
            raise KeyboardInterrupt
        out = {"smoke": self.version + "\n",
               "freeze": "fastapi==0.110.0\n-e file:///projekt\nkassivo @ file:///x\nuvicorn==0.27.0\n"}
        return subprocess.CompletedProcess(args, 0, out.get(kind, ""), "")

    def kinds(self):
        out = []
        for args, _ in self.calls:
            out.append("freeze" if "freeze" in args else "restore" if "-r" in args
                       else "pip" if "pip" in args else "smoke")
        return out


def _apply(env, run=None, **kw):
    kw.setdefault("port_in_use", lambda: False)
    kw.setdefault("package_dir", env["project"] / "src" / "kassivo")
    return update.apply_update(env["project"], env["zip"], env["db"], current="1.2.0",
                               run=run or FakeRun(), python="python-test", now=NOW, **kw)


def _db_hash(db):
    return hashlib.sha256(Path(db).read_bytes()).hexdigest()


def test_apply_swaps_program_files_and_keeps_local_data(env):
    before_db = _db_hash(env["db"])
    run = FakeRun()
    result = _apply(env, run)
    p = env["project"]
    assert result.version == "1.3.0"
    assert '__version__ = "1.3.0"' in (p / "src" / "kassivo" / "__init__.py").read_text()
    assert (p / "src" / "kassivo" / "neu_modul.py").is_file()
    # ohne Liste der Paketdateien weiß KASSIVO nicht, ob alt_modul.py zum Programm gehört
    assert (p / "src" / "kassivo" / "alt_modul.py").is_file()
    assert (p / ".venv" / "marker").read_text() == "venv"
    assert (p / ".git" / "HEAD").read_text().startswith("ref:")
    assert (p / "eigene-notiz.txt").read_text() == "bleibt"
    assert _db_hash(env["db"]) == before_db  # Datenbank nie geschrieben
    old = result.rollback_dir
    assert '"1.2.0"' in (old / "src" / "kassivo" / "__init__.py").read_text()  # alter Stand
    assert old.parent == p / ".update"
    assert not (p / ".update" / "journal.json").exists()
    assert sorted(x.name for x in (p / ".update").iterdir()
                  if x.is_dir()) == [old.name]  # Entpackordner aufgeräumt


def test_user_files_in_program_folders_stay(env):
    """Nicht versionierte eigene Dateien verschwinden nicht (dateiweiser Austausch)."""
    p = env["project"]
    own = {".claude/settings.local.json": "{}", "docs/design/referenz/bild.png": "png",
           "docs/eigene.md": "notiz", "tests/test_eigen.py": "x = 1",
           ".github/eigene.yml": "y", "src/kassivo/web/eigene.html": "z"}
    for rel, text in own.items():
        (p / rel).parent.mkdir(parents=True, exist_ok=True)
        (p / rel).write_text(text)
    archive = make_archive("1.3.0", {"kassivo-1.3.0/docs/plan.md": "neu",
                                     "kassivo-1.3.0/tests/test_neu.py": "y = 2",
                                     "kassivo-1.3.0/.claude/agents/a.md": "a",
                                     "kassivo-1.3.0/.github/workflows/t.yml": "t",
                                     "kassivo-1.3.0/src/kassivo/web/app.py": "neu"})
    env["zip"].write_bytes(archive)
    (env["updates"] / "kassivo-1.3.0.zip.sha256").write_text(sha(archive))
    _apply(env)
    for rel, text in own.items():
        assert (p / rel).read_text() == text, rel
    assert (p / "docs" / "plan.md").read_text() == "neu"


def test_files_of_previous_package_are_removed_via_manifest(env):
    p = env["project"]
    _apply(env)
    manifest = (p / ".update" / "dateien.txt").read_text("utf-8").splitlines()
    assert "src/kassivo/neu_modul.py" in manifest and "README.md" in manifest
    # nächste Version ohne neu_modul.py: es war Paketinhalt und wird weggeräumt
    archive = make_archive("1.4.0")
    z = env["updates"] / "kassivo-1.4.0.zip"
    z.write_bytes(archive)
    (env["updates"] / "kassivo-1.4.0.zip.sha256").write_text(sha(archive))
    (p / "eigene-notiz.txt").write_text("bleibt")
    update.apply_update(p, z, env["db"], current="1.3.0", run=FakeRun("1.4.0"), python="p",
                        now=NOW, port_in_use=lambda: False, package_dir=p / "src" / "kassivo")
    assert not (p / "src" / "kassivo" / "neu_modul.py").exists()
    assert (p / "src" / "kassivo" / "alt_modul.py").is_file()  # nie Paketinhalt gewesen
    assert (p / "eigene-notiz.txt").read_text() == "bleibt"


def test_apply_backs_up_database_first(env):
    result = _apply(env)
    backup = env["db"].parent / "backups" / result.backup_name
    conn = sqlite3.connect(backup)
    assert conn.execute("select name from member").fetchall() == [("Max Mustermann",)]
    conn.close()


def test_apply_without_database_needs_no_backup(env):
    env["db"].unlink()
    result = _apply(env)
    assert result.backup_name is None
    assert not env["db"].exists()  # wird nicht angelegt


def test_apply_runs_smoke_test_only_when_dependencies_unchanged(env):
    run = FakeRun()
    _apply(env, run)
    assert len(run.calls) == 1
    args, kw = run.calls[0]
    assert args[:3] == ["python-test", "-I", "-c"] and "kassivo.web.app" in args[3]
    assert kw["cwd"] == env["project"] and kw.get("timeout")


def test_apply_runs_pip_when_dependencies_change(env):
    # die installierte Fassung hat andere Abhängigkeiten als das Paket
    (env["project"] / "pyproject.toml").write_text(
        '[project]\nname = "kassivo"\nversion = "1.2.0"\ndependencies = ["fastapi>=0.100"]\n')
    run = FakeRun()
    _apply(env, run)
    assert run.kinds() == ["freeze", "pip", "smoke"]
    assert run.calls[0][0] == ["python-test", "-m", "pip", "freeze"]
    assert run.calls[1][0][:6] == ["python-test", "-m", "pip", "install", "-e", "."]


def _change_deps(env):
    (env["project"] / "pyproject.toml").write_text(
        '[project]\nname = "kassivo"\nversion = "1.2.0"\ndependencies = []\n')


@pytest.mark.parametrize("fail", ["smoke", "pip"])
def test_failed_step_rolls_back(env, fail):
    if fail == "pip":
        _change_deps(env)
    before = _snapshot(env["project"])
    with pytest.raises(update.UpdateError) as info:
        _apply(env, FakeRun(fail={fail}))
    assert "zurückgesetzt" in str(info.value)
    assert _snapshot(env["project"]) == before
    work = env["project"] / ".update"
    assert not (work / "journal.json").exists()
    leftovers = [x.name for x in work.iterdir() if x.is_dir()]
    assert leftovers == [], leftovers  # neu-, fehl- und leerer alt-Ordner aufgeräumt


def test_rollback_after_pip_restores_previous_packages(env):
    _change_deps(env)
    run = FakeRun(fail={"smoke"})
    with pytest.raises(update.UpdateError) as info:
        _apply(env, run)
    assert run.kinds() == ["freeze", "pip", "smoke", "restore"]
    freeze_arg = run.calls[3][0][-1]
    assert run.calls[3][0][:5] == ["python-test", "-m", "pip", "install", "-r"]
    assert "Python-Pakete auf den alten Stand" in str(info.value)
    assert "kassivo" not in Path(freeze_arg).read_text() if Path(freeze_arg).exists() else True


def test_failed_package_restore_gives_repair_command(env):
    _change_deps(env)
    with pytest.raises(update.UpdateError) as info:
        _apply(env, FakeRun(fail={"smoke", "restore"}))
    text = str(info.value)
    assert "pip install -r" in text and "Reparatur" in text
    assert (env["project"] / ".update").exists()


def test_keyboard_interrupt_rolls_back_and_propagates(env):
    """I1: auch Strg+C (BaseException) setzt zurück und wird danach weitergeworfen."""
    before = _snapshot(env["project"])
    with pytest.raises(KeyboardInterrupt):
        _apply(env, FakeRun(fail={"smoke-interrupt"}))
    assert _snapshot(env["project"]) == before
    assert not (env["project"] / ".update" / "journal.json").exists()


def test_transient_permission_error_is_retried(env, monkeypatch):
    """I3: OneDrive sperrt kurz; der Austausch versucht es erneut."""
    real = os.replace
    hits = {"n": 0}

    def locked_twice(a, b):
        if str(a).endswith("README.md") and hits["n"] < 2:
            hits["n"] += 1
            raise PermissionError("gesperrt")
        return real(a, b)
    monkeypatch.setattr(os, "replace", locked_twice)
    monkeypatch.setattr(update_recover.time, "sleep", lambda s: None)
    _apply(env)
    assert hits["n"] == 2
    assert (env["project"] / "README.md").read_text() == "# KASSIVO\n"


def test_permanent_permission_error_rolls_back(env, monkeypatch):
    real = os.replace
    before = _snapshot(env["project"])

    def locked(a, b):
        if str(a).endswith("start.bat") and "n-" in str(a):
            raise PermissionError("gesperrt")
        return real(a, b)
    monkeypatch.setattr(os, "replace", locked)
    monkeypatch.setattr(update_recover.time, "sleep", lambda s: None)
    with pytest.raises(update.UpdateError):
        _apply(env)
    assert _snapshot(env["project"]) == before


def _crash_midway(env, monkeypatch, after=5):
    """Simuliert einen harten Abbruch (Strom weg): Austausch stoppt, kein Rückweg läuft."""
    real = os.replace
    count = {"n": 0}

    class Killed(BaseException):
        pass

    def dying(a, b):
        if "n-" in str(a):
            count["n"] += 1
            if count["n"] == after:
                raise Killed()
        return real(a, b)
    with pytest.MonkeyPatch.context() as mp:  # eigener Patch: die Netzsperre bleibt aktiv
        mp.setattr(os, "replace", dying)
        mp.setattr(update_recover, "recover", lambda *a, **k: [])
        with pytest.raises(Killed):
            _apply(env)


def test_journal_survives_crash_and_standalone_script_restores(env, monkeypatch):
    """I2: wiederherstellen.py läuft ohne kassivo (src halb ausgetauscht) und setzt zurück."""
    before = _snapshot(env["project"])
    _crash_midway(env, monkeypatch)
    p = env["project"]
    assert (p / ".update" / "journal.json").is_file()
    assert _snapshot(p) != before
    script = p / ".update" / "wiederherstellen.py"
    assert script.is_file()
    r = subprocess.run([sys.executable, "-I", str(script), str(p)], cwd=p.parent,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "zurückgesetzt" in r.stdout
    assert _snapshot(p) == before
    assert not (p / ".update" / "journal.json").exists()


def test_main_detects_unfinished_update_and_recovers_first(env, monkeypatch):
    before = _snapshot(env["project"])
    _crash_midway(env, monkeypatch, after=3)
    asked = []
    code, text = _main(env, "j", ask=lambda q: asked.append(q) or "j")
    assert code == 2 and "unvollständig" in text
    assert asked == []  # erst zurücksetzen, nicht gleich weiter installieren
    assert _snapshot(env["project"]) == before


def test_recovery_is_repeatable(env, monkeypatch):
    before = _snapshot(env["project"])
    _crash_midway(env, monkeypatch)
    p = env["project"]
    journal = (p / ".update" / "journal.json").read_text()
    update_recover.recover(p)
    (p / ".update" / "journal.json").write_text(journal)  # Abbruch während der Wiederherstellung
    update_recover.recover(p)
    assert _snapshot(p) == before


def test_smoke_test_with_wrong_version_rolls_back(env):
    before = _snapshot(env["project"])
    with pytest.raises(update.UpdateError):
        _apply(env, FakeRun(version="1.2.0"))
    assert _snapshot(env["project"]) == before


def test_failure_during_swap_rolls_back(env, monkeypatch):
    before = _snapshot(env["project"])
    real = os.replace
    count = {"n": 0}

    def flaky(a, b):
        count["n"] += 1
        if count["n"] == 4:  # mitten im Austausch, kein vorübergehender Fehler
            raise OSError("Datenträger defekt")
        return real(a, b)
    monkeypatch.setattr(update.os, "replace", flaky)
    with pytest.raises(update.UpdateError) as info:
        _apply(env)
    assert "zurückgesetzt" in str(info.value)
    assert _snapshot(env["project"]) == before


def test_checksum_mismatch_changes_nothing(env):
    (env["updates"] / "kassivo-1.3.0.zip.sha256").write_text("0" * 64)
    before = _snapshot(env["project"])
    with pytest.raises(update.UpdateError):
        _apply(env)
    assert _snapshot(env["project"]) == before
    assert not (env["db"].parent / "backups").exists()


def test_missing_checksum_file_changes_nothing(env):
    (env["updates"] / "kassivo-1.3.0.zip.sha256").unlink()
    with pytest.raises(update.UpdateError):
        _apply(env)


def test_running_kassivo_blocks_update(env):
    before = _snapshot(env["project"])
    with pytest.raises(update.UpdateError) as info:
        _apply(env, port_in_use=lambda: True)
    assert "beenden" in str(info.value)
    assert _snapshot(env["project"]) == before


def test_non_editable_install_is_refused(env, tmp_path):
    with pytest.raises(update.UpdateError) as info:
        _apply(env, package_dir=tmp_path / "site-packages" / "kassivo")
    assert "pip install -e" in str(info.value)


def test_database_inside_replaced_folder_is_refused(env):
    db = env["project"] / "src" / "kassivo.db"
    sqlite3.connect(db).close()
    before = db.read_bytes()
    with pytest.raises(update.UpdateError):
        update.apply_update(env["project"], env["zip"], db, current="1.2.0", run=FakeRun(),
                            python="p", now=NOW, port_in_use=lambda: False,
                            package_dir=env["project"] / "src" / "kassivo")
    assert db.read_bytes() == before


def test_not_newer_is_refused(env):
    with pytest.raises(update.UpdateError):
        update.apply_update(env["project"], env["zip"], env["db"], current="1.3.0",
                            run=FakeRun(), python="p", now=NOW, port_in_use=lambda: False,
                            package_dir=env["project"] / "src" / "kassivo")


def test_backup_failure_aborts_before_any_change(env):
    env["db"].write_bytes(b"keine Datenbank" * 100)
    before = _snapshot(env["project"])
    with pytest.raises(update.UpdateError) as info:
        _apply(env)
    assert "Sicherung" in str(info.value)
    assert _snapshot(env["project"]) == before


def test_apply_writes_log_without_personal_data(env):
    _apply(env)
    log = (env["updates"] / "update.log").read_text("utf-8")
    assert "1.2.0" in log and "1.3.0" in log
    assert "Max Mustermann" not in log
    assert str(env["project"].parent) not in log  # keine vollen Pfade (Benutzername)


def test_error_messages_contain_no_full_paths(env, monkeypatch):
    real = os.replace

    def broken(a, b):
        if "a-" in str(b) or "a-" in str(a):
            raise OSError("kaputt")
        return real(a, b)
    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(update.UpdateError) as info:
        _apply(env)
    assert str(env["project"].parent) not in str(info.value)


def test_unreadable_pyproject_is_update_error(env):
    (env["project"] / "pyproject.toml").write_bytes(b"\xff\xfe kaputt")
    with pytest.raises(update.UpdateError):
        _apply(env)


# --- Paket finden und Befehl --------------------------------------------------------------

def test_find_package_takes_newest_newer_verified(env):
    u = env["updates"]
    (u / "kassivo-1.2.0.zip").write_bytes(b"x")
    (u / "kassivo-1.2.0.zip.sha256").write_text("0" * 64)
    (u / "kassivo-1.4.0.zip").write_bytes(b"ohne Pruefsummen-Datei")
    assert update.find_package(u, "1.2.0") == env["zip"]
    assert update.find_package(u, "1.3.0") is None
    assert update.find_package(u.parent / "fehlt", "1.2.0") is None


def _main(env, answer, **over):
    out = []
    kw = {"db_path": env["db"], "project_dir": env["project"],
          "package_dir": env["project"] / "src" / "kassivo", "current": "1.2.0",
          "ask": lambda prompt: answer, "out": out.append, "run": FakeRun(),
          "port_in_use": lambda: False}
    kw.update(over)
    code = update.main(["apply"], **kw)
    return code, "\n".join(out)


def test_main_treats_closed_input_as_no(env):
    def closed(prompt):
        raise EOFError
    code, text = _main(env, "", ask=closed)
    assert code == 1 and "abgebrochen" in text


def test_main_asks_and_aborts_on_no(env):
    before = _snapshot(env["project"])
    code, text = _main(env, "n")
    assert code == 1 and "abgebrochen" in text
    assert _snapshot(env["project"]) == before


def test_main_installs_on_yes(env):
    code, text = _main(env, "j")
    assert code == 0
    assert "start.bat" in text
    assert '"1.3.0"' in (env["project"] / "src" / "kassivo" / "__init__.py").read_text()


def test_main_without_package(env):
    env["zip"].unlink()
    code, text = _main(env, "j")
    assert code == 1 and "Kein heruntergeladenes Update" in text


@pytest.mark.parametrize("over", [
    {"port_in_use": lambda: True},
    {"package_dir": Path("/anderswo/kassivo")},
])
def test_main_checks_before_asking(env, over):
    asked = []
    code, text = _main(env, "j", ask=lambda q: asked.append(q) or "j", **over)
    assert code == 2 and asked == []


def test_main_checks_checksum_before_asking(env):
    (env["updates"] / "kassivo-1.3.0.zip.sha256").write_text("0" * 64)
    asked = []
    code, _ = _main(env, "j", ask=lambda q: asked.append(q) or "j")
    assert code == 2 and asked == []


def test_main_warns_about_cloud_folder(env, tmp_path):
    cloud = tmp_path / "OneDrive - Firma" / "projekt"
    cloud.parent.mkdir()
    env["project"].rename(cloud)
    env["project"] = cloud
    code, text = _main(env, "n")
    assert "OneDrive-Synchronisation pausieren" in text


def test_main_reports_errors_without_traceback(env):
    code, text = _main(env, "j", port_in_use=lambda: True)
    assert code == 2
    assert "beenden" in text and "Traceback" not in text


def test_main_rejects_unknown_command(env):
    assert update.main(["irgendwas"], out=lambda s: None) == 2


# --- update.bat ------------------------------------------------------------------------

BAT = ROOT / "update.bat"


def test_update_bat_is_ascii_crlf_and_uses_venv():
    raw = BAT.read_bytes()
    text = raw.decode("ascii")
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert r'".venv\Scripts\python.exe" -I -m kassivo.update apply' in text
    assert 'cd /d "%~dp0"' in text
    assert "virtuelle Umgebung" in text


def test_update_bat_runs_inside_one_block():
    """cmd liest den Block ganz ein: der Austausch von update.bat stört den Lauf nicht."""
    text = BAT.read_bytes().decode("ascii")
    lines = [ln.strip() for ln in text.splitlines()]
    start = lines.index("(")
    assert lines[-1] == ")" or lines[-1] == ""
    body = "\n".join(lines[start:])
    assert "exit /b" in body and "pause" in body
    assert "start.bat" not in body.split("exit /b")[-1]  # kein automatischer Neustart
    echoes = [ln for ln in lines[start + 1:] if ln.startswith("echo")]
    assert echoes and not any(")" in ln for ln in echoes)  # ")" beendet sonst den Block


def test_update_bat_changes_dir_before_delayed_expansion():
    """Mit EnableDelayedExpansion gingen "!" im Pfad verloren: cd vorher."""
    text = BAT.read_bytes().decode("ascii")
    assert text.index('cd /d "%~dp0"') < text.index("EnableDelayedExpansion")


def test_update_bat_recovers_unfinished_update_without_kassivo():
    text = BAT.read_bytes().decode("ascii")
    assert r'if exist ".update\journal.json"' in text
    assert r'-I ".update\wiederherstellen.py" .' in text
    assert text.index("wiederherstellen.py") < text.index("-m kassivo.update apply")


def test_recover_module_uses_only_stdlib():
    src = (ROOT / "src" / "kassivo" / "update_recover.py").read_text("utf-8")
    assert "import kassivo" not in src and "from kassivo" not in src
