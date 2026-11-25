"""Pfad der Datenbank und Startmeldung. Berührt nie das echte Home oder data/."""
import importlib
import sys
from pathlib import Path

import pytest

from kassivo import settings
from kassivo.settings import cloud_warning, default_db_path, startup_lines


def test_windows_localappdata(tmp_path):
    env = {"LOCALAPPDATA": str(tmp_path / "Local")}
    assert default_db_path(env=env, platform="win32", home=tmp_path / "h") == \
        tmp_path / "Local" / "KASSIVO" / "kassivo.db"


def test_windows_fallback_without_localappdata(tmp_path):
    assert default_db_path(env={}, platform="win32", home=tmp_path) == \
        tmp_path / "AppData" / "Local" / "KASSIVO" / "kassivo.db"


def test_linux_xdg(tmp_path):
    env = {"XDG_DATA_HOME": str(tmp_path / "xdg")}
    assert default_db_path(env=env, platform="linux", home=tmp_path / "h") == \
        tmp_path / "xdg" / "kassivo" / "kassivo.db"


def test_linux_fallback(tmp_path):
    assert default_db_path(env={}, platform="linux", home=tmp_path) == \
        tmp_path / ".local" / "share" / "kassivo" / "kassivo.db"


def test_env_override_wins_and_is_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = {"KASSIVO_DB": "sub/k.db", "LOCALAPPDATA": str(tmp_path / "L")}
    p = default_db_path(env=env, platform="win32", home=tmp_path)
    assert p.is_absolute() and p == (tmp_path / "sub" / "k.db").resolve()


def test_default_uses_process_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KASSIVO_DB", str(tmp_path / "x.db"))
    assert default_db_path() == (tmp_path / "x.db").resolve()


@pytest.mark.parametrize("p", [
    r"C:\Users\x\OneDrive - Firma GmbH\kasse\kassivo.db", "/home/x/Dropbox/kassivo.db",
    "/Users/x/Google Drive/k.db", r"C:\Users\x\iCloudDrive\k.db", "/home/x/onedrive/k.db"])
def test_cloud_warning(p):
    w = cloud_warning(Path(p))
    assert w is not None and "Warnung" in w and "Cloud" in w and "KASSIVO_DB" in w


def test_no_cloud_warning_for_local(tmp_path):
    assert cloud_warning(tmp_path / "Local" / "KASSIVO" / "kassivo.db") is None


def test_startup_lines(tmp_path):
    db = tmp_path / "OneDrive - X" / "kassivo.db"
    text = "\n".join(startup_lines(db))
    assert str(db) in text and str(db.parent / "backups") in text
    assert "Warnung" in text


def test_no_dead_db_path_constant():
    assert not hasattr(settings, "DB_PATH")


def test_import_creates_no_file(tmp_path, monkeypatch):
    target = tmp_path / "neu" / "kassivo.db"
    monkeypatch.setenv("KASSIVO_DB", str(target))
    importlib.reload(settings)
    sys.modules.pop("kassivo.__main__", None)
    importlib.import_module("kassivo.__main__")
    assert not target.exists() and not target.parent.exists()


def test_main_prints_path_and_creates_parent(tmp_path, monkeypatch, capsys):
    target = tmp_path / "neu" / "kassivo.db"
    monkeypatch.setenv("KASSIVO_DB", str(target))
    main_mod = importlib.import_module("kassivo.__main__")
    calls = []
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda app, **kw: calls.append(kw))
    main_mod.main()
    out = capsys.readouterr().out
    assert str(target.resolve()) in out
    assert str(target.resolve().parent / "backups") in out
    assert target.parent.is_dir()
    assert len(calls) == 1 and calls[0]["host"] == "127.0.0.1" and calls[0]["port"] == 8000
