"""Seite Einstellungen > Update: Opt-in, Prüfen, Herunterladen, Herkunftsschutz. Kein Netz."""
import html
import shutil
import subprocess
from pathlib import Path

import pytest

import kassivo
from kassivo import update
from kassivo.db import connect, get_setting
from tests.helpers import make_client
from tests.update_helpers import FakeNet, full_net, release_json

ROOT = Path(__file__).resolve().parent.parent
NEWER = "99.0.0"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "daten" / "kassivo.db"


def _client(db_path, net):
    c = make_client(db_path)
    c.app.state.update_fetch = net
    return c


def _enable(c):
    assert c.post("/settings/update", data={"enabled": "1"}).status_code == 303


def test_page_default_off_and_no_network(db_path):
    net = full_net(NEWER)
    c = _client(db_path, net)
    r = c.get("/settings/update")
    assert r.status_code == 200
    text = html.unescape(r.text)
    assert "Die Update-Prüfung ist ausgeschaltet" in text
    assert kassivo.__version__ in text
    assert '<a href="/wiki/update">Hilfe zu dieser Seite</a>' in r.text
    assert 'action="/settings/update/check"' not in r.text
    assert net.calls == []


@pytest.mark.parametrize("path", ["/settings/update/check", "/settings/update/download"])
def test_opt_in_off_means_no_network_call(db_path, path):
    net = full_net(NEWER)
    c = _client(db_path, net)
    r = c.post(path)
    assert r.status_code == 409
    assert "ausgeschaltet" in html.unescape(r.text)
    assert net.calls == []


def test_toggle_is_stored(db_path):
    c = _client(db_path, FakeNet())
    r = c.post("/settings/update", data={"enabled": "1"})
    assert r.headers["location"] == "/settings/update?ok=1"
    conn = connect(db_path)
    assert get_setting(conn, update.SETTING_KEY) == "1"
    conn.close()
    c.post("/settings/update", data={})
    conn = connect(db_path)
    assert get_setting(conn, update.SETTING_KEY) == "0"
    conn.close()


def test_get_never_calls_network_even_when_enabled(db_path):
    net = full_net(NEWER)
    c = _client(db_path, net)
    _enable(c)
    r = c.get("/settings/update")
    assert 'action="/settings/update/check"' in r.text
    assert net.calls == []


def test_check_shows_newer_release_with_escaped_notes(db_path):
    net = FakeNet({update.API_URL: release_json(NEWER, body="<script>alert(1)</script> Neu")})
    c = _client(db_path, net)
    _enable(c)
    r = c.post("/settings/update/check")
    assert r.status_code == 200
    assert f"Version {NEWER} ist verfügbar" in html.unescape(r.text)
    assert "<script>alert" not in r.text and "&lt;script&gt;" in r.text
    assert 'action="/settings/update/download"' in r.text
    assert net.calls == [update.API_URL]


def test_check_current_version(db_path):
    net = FakeNet({update.API_URL: release_json(kassivo.__version__)})
    c = _client(db_path, net)
    _enable(c)
    text = html.unescape(c.post("/settings/update/check").text)
    assert "aktuell" in text
    assert 'action="/settings/update/download"' not in text


def test_check_without_package_offers_no_download(db_path):
    net = FakeNet({update.API_URL: release_json(NEWER, assets=[])})
    c = _client(db_path, net)
    _enable(c)
    text = html.unescape(c.post("/settings/update/check").text)
    assert update.E_NO_PACKAGE in text
    assert 'action="/settings/update/download"' not in text


@pytest.mark.parametrize("error", [update.E_TIMEOUT, update.E_NETWORK, update.E_TLS])
def test_check_network_errors_show_message(db_path, error):
    net = FakeNet({update.API_URL: update.UpdateError(error)})
    c = _client(db_path, net)
    _enable(c)
    r = c.post("/settings/update/check")
    assert r.status_code == 200
    assert error in html.unescape(r.text)


def test_download_saves_verified_package(db_path):
    net = full_net(NEWER)
    c = _client(db_path, net)
    _enable(c)
    r = c.post("/settings/update/download")
    assert r.status_code == 200
    text = html.unescape(r.text)
    assert f"Update {NEWER} ist heruntergeladen und geprüft" in text
    assert "update.bat" in text
    target = db_path.parent / "updates" / f"kassivo-{NEWER}.zip"
    assert target.is_file()
    assert all(u.startswith(("https://api.github.com/", update.DOWNLOAD_PREFIX))
               for u in net.calls)
    assert f"Update {NEWER} ist heruntergeladen" in html.unescape(c.get("/settings/update").text)


def test_download_wrong_checksum_saves_nothing(db_path):
    net = full_net(NEWER, sums=f"{'0' * 64}  kassivo-{NEWER}.zip\n".encode())
    c = _client(db_path, net)
    _enable(c)
    text = html.unescape(c.post("/settings/update/download").text)
    assert update.E_CHECKSUM in text
    assert not (db_path.parent / "updates" / f"kassivo-{NEWER}.zip").exists()


def test_download_not_newer_downloads_nothing(db_path):
    net = full_net(kassivo.__version__)
    c = _client(db_path, net)
    _enable(c)
    text = html.unescape(c.post("/settings/update/download").text)
    assert "aktuell" in text
    assert net.calls == [update.API_URL]


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
])
@pytest.mark.parametrize("path", ["/settings/update", "/settings/update/check",
                                  "/settings/update/download"])
def test_foreign_origin_is_rejected_before_any_network_call(db_path, headers, path):
    net = full_net(NEWER)
    c = _client(db_path, net)
    _enable(c)
    r = c.post(path, data={"enabled": "0"}, headers=headers)
    assert r.status_code == 403
    assert net.calls == []
    conn = connect(db_path)
    assert get_setting(conn, update.SETTING_KEY) == "1"
    conn.close()


def test_foreign_host_is_rejected(db_path):
    net = full_net(NEWER)
    c = _client(db_path, net)
    assert c.post("/settings/update/check", headers={"Host": "evil.example"}).status_code == 400
    assert net.calls == []


def test_get_only_for_page(db_path):
    c = _client(db_path, FakeNet())
    assert c.get("/settings/update/check").status_code == 405


def test_security_headers_on_update_page(db_path):
    r = _client(db_path, FakeNet()).get("/settings/update")
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_settings_page_links_to_update(db_path):
    r = _client(db_path, FakeNet()).get("/settings/fees")
    assert 'href="/settings/update"' in r.text


def test_default_client_is_the_real_one_but_blocked_in_tests(db_path):
    """Ohne Injektion nutzt die Seite urllib_fetch; die Autouse-Fixture sperrt es hier."""
    c = make_client(db_path, raise_server_exceptions=True)
    _enable(c)
    with pytest.raises(RuntimeError):
        c.post("/settings/update/check")


def test_update_template_is_own_file_and_layout_untouched():
    t = ROOT / "src" / "kassivo" / "web" / "templates" / "update.html"
    assert t.is_file()
    assert '{% extends "base.html" %}' in t.read_text("utf-8")


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(),
                    reason="braucht ein Git-Checkout")
def test_real_repository_archive_passes_check(tmp_path):
    """Das Archiv, das release.yml baut, besteht die eigene Prüfung."""
    version = kassivo.__version__
    out = tmp_path / f"kassivo-{version}.zip"
    subprocess.run(["git", "archive", "--format=zip", f"--prefix=kassivo-{version}/",
                    "-o", str(out), "HEAD"], cwd=ROOT, check=True, capture_output=True)
    with open(out, "rb") as f:
        tops = update.check_archive(f, version)
    assert {"src", "pyproject.toml", "start.bat", "update.bat"} <= set(tops)
    assert out.stat().st_size < update.MAX_ZIP_BYTES
