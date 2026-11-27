"""Update-Prüfung, Download, Prüfsumme und Archiv-Prüfung. Keine echten Netzaufrufe."""
import io
import json
import socket
import ssl
import stat
import urllib.error
import urllib.request
import zipfile

import pytest

from kassivo import update
from tests.update_helpers import (
    SUMS_URL, ZIP_URL, FakeNet, full_net, make_archive, release_json, sha,
)


# --- Versionsvergleich -----------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1.2.0", (1, 2, 0)), ("v1.2.0", (1, 2, 0)), ("10.0.3", (10, 0, 3)), ("0.0.0", (0, 0, 0)),
])
def test_parse_version_valid(text, expected):
    assert update.parse_version(text) == expected


@pytest.mark.parametrize("text", [
    "", "1.2", "1.2.3.4", "1.2.3-rc1", "1.2.3+build", "01.2.3", "1.02.3", "V1.2.3", " 1.2.3",
    "1.2.3 ", "vv1.2.3", "1.2.x", "１.２.３", "1234567.0.0", None, 123,
])
def test_parse_version_invalid(text):
    assert update.parse_version(text) is None


@pytest.mark.parametrize("candidate,current,newer", [
    ("1.3.0", "1.2.0", True), ("1.10.0", "1.9.9", True), ("2.0.0", "1.99.99", True),
    ("1.2.1", "1.2.0", True), ("1.2.0", "1.2.0", False), ("1.1.9", "1.2.0", False),
    ("v1.3.0", "1.2.0", True), ("1.3.0-rc1", "1.2.0", False), ("kaputt", "1.2.0", False),
    ("1.3.0", "kaputt", False),
])
def test_is_newer(candidate, current, newer):
    assert update.is_newer(candidate, current) is newer


# --- Release abfragen -------------------------------------------------------------------

def test_check_latest_reads_fixed_url_only():
    net = full_net("1.3.0")
    rel = update.check_latest(net)
    assert net.calls == [update.API_URL]
    assert update.API_URL == "https://api.github.com/repos/Nirodo/KASSIVO/releases/latest"
    assert rel.version == "1.3.0"
    assert rel.zip_url == ZIP_URL.format(v="1.3.0")
    assert rel.sums_url == SUMS_URL.format(v="1.3.0")
    assert rel.notes == "Neu: Update-Funktion."
    assert rel.page_url == "https://github.com/Nirodo/KASSIVO/releases/tag/v1.3.0"
    assert rel.has_package


def test_check_latest_truncates_notes():
    net = FakeNet({update.API_URL: release_json(body="x" * (update.MAX_NOTES + 100))})
    assert len(update.check_latest(net).notes) == update.MAX_NOTES


@pytest.mark.parametrize("over", [{"draft": True}, {"prerelease": True}])
def test_check_latest_rejects_draft_and_prerelease(over):
    net = FakeNet({update.API_URL: release_json(**over)})
    with pytest.raises(update.UpdateError):
        update.check_latest(net)


@pytest.mark.parametrize("raw", [b"{kaputt", b"[]", b'"text"', "ü".encode("latin-1"),
                                 json.dumps({"tag_name": "v1.3"}).encode(),
                                 json.dumps({"tag_name": None}).encode()])
def test_check_latest_rejects_bad_answers(raw):
    with pytest.raises(update.UpdateError):
        update.check_latest(FakeNet({update.API_URL: raw}))


@pytest.mark.parametrize("error", [
    update.UpdateError(update.E_NETWORK), update.UpdateError(update.E_TIMEOUT),
])
def test_check_latest_passes_network_errors_as_update_error(error):
    with pytest.raises(update.UpdateError) as info:
        update.check_latest(FakeNet({update.API_URL: error}))
    assert str(info.value) == str(error)


def test_check_latest_without_assets_has_no_package():
    rel = update.check_latest(FakeNet({update.API_URL: release_json(assets=[])}))
    assert rel.version == "1.3.0" and not rel.has_package


@pytest.mark.parametrize("url", [
    "https://evil.example/kassivo-1.3.0.zip",
    "http://github.com/Nirodo/KASSIVO/releases/download/v1.3.0/kassivo-1.3.0.zip",
    "https://github.com/Andere/KASSIVO/releases/download/v1.3.0/kassivo-1.3.0.zip",
    "https://github.com/Nirodo/KASSIVO/releases/download/v1.2.0/kassivo-1.3.0.zip",
    "https://github.com/Nirodo/KASSIVO/releases/download/v1.3.0/../x/kassivo-1.3.0.zip",
])
def test_foreign_asset_urls_are_ignored(url):
    assets = [{"name": "kassivo-1.3.0.zip", "browser_download_url": url},
              {"name": "SHA256SUMS", "browser_download_url": SUMS_URL.format(v="1.3.0")}]
    rel = update.check_latest(FakeNet({update.API_URL: release_json(assets=assets)}))
    assert not rel.has_package


def test_page_url_only_from_project():
    raw = release_json(html_url="https://evil.example/")
    assert update.check_latest(FakeNet({update.API_URL: raw})).page_url == update.RELEASES_PAGE


# --- Herunterladen und Prüfsumme ---------------------------------------------------------

def test_download_saves_verified_archive(tmp_path):
    net = full_net("1.3.0")
    rel = update.check_latest(net)
    path = update.download_release(rel, tmp_path / "updates", net)
    assert path == tmp_path / "updates" / "kassivo-1.3.0.zip"
    data = path.read_bytes()
    assert data == make_archive("1.3.0")
    assert (tmp_path / "updates" / "kassivo-1.3.0.zip.sha256").read_text().strip() == sha(data)
    assert set(net.calls[1:]) == {ZIP_URL.format(v="1.3.0"), SUMS_URL.format(v="1.3.0")}


def test_wrong_checksum_saves_nothing(tmp_path):
    archive = make_archive("1.3.0")
    net = full_net("1.3.0", archive=archive, sums=f"{'0' * 64}  kassivo-1.3.0.zip\n".encode())
    with pytest.raises(update.UpdateError) as info:
        update.download_release(update.check_latest(net), tmp_path, net)
    assert "Prüfsumme" in str(info.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("sums", [
    b"", b"kaputt", b"abc  kassivo-1.3.0.zip\n", b"%s  andere.zip\n",
    b"%s  kassivo-1.3.0.zip\n%s  kassivo-1.3.0.zip\n",
])
def test_bad_sums_file_saves_nothing(tmp_path, sums):
    archive = make_archive("1.3.0")
    h = sha(archive).encode()
    net = full_net("1.3.0", archive=archive, sums=sums.replace(b"%s", h))
    with pytest.raises(update.UpdateError):
        update.download_release(update.check_latest(net), tmp_path, net)
    assert list(tmp_path.iterdir()) == []


def test_sums_file_accepts_binary_marker_and_other_files(tmp_path):
    archive = make_archive("1.3.0")
    sums = f"{'1' * 64}  kassivo-1.3.0-py3-none-any.whl\n{sha(archive).upper()} *kassivo-1.3.0.zip\n"
    net = full_net("1.3.0", archive=archive, sums=sums.encode())
    assert update.download_release(update.check_latest(net), tmp_path, net).is_file()


def test_api_digest_must_match(tmp_path):
    net = full_net("1.3.0", digest="sha256:" + "0" * 64)
    with pytest.raises(update.UpdateError):
        update.download_release(update.check_latest(net), tmp_path, net)
    assert list(tmp_path.iterdir()) == []


def test_api_digest_matching_is_fine(tmp_path):
    archive = make_archive("1.3.0")
    net = full_net("1.3.0", archive=archive, digest="sha256:" + sha(archive))
    assert update.download_release(update.check_latest(net), tmp_path, net).is_file()


def test_download_without_package_fails(tmp_path):
    net = FakeNet({update.API_URL: release_json(assets=[])})
    with pytest.raises(update.UpdateError):
        update.download_release(update.check_latest(net), tmp_path, net)
    assert net.calls == [update.API_URL]


def test_download_with_correct_checksum_but_bad_archive_saves_nothing(tmp_path):
    archive = make_archive("1.3.0", {"kassivo-1.3.0/../boese.txt": "x"})
    net = full_net("1.3.0", archive=archive)
    with pytest.raises(update.UpdateError):
        update.download_release(update.check_latest(net), tmp_path, net)
    assert list(tmp_path.iterdir()) == []


def test_download_network_error_saves_nothing(tmp_path):
    net = full_net("1.3.0")
    net.responses[ZIP_URL.format(v="1.3.0")] = update.UpdateError(update.E_TIMEOUT)
    with pytest.raises(update.UpdateError):
        update.download_release(update.check_latest(net), tmp_path, net)
    assert list(tmp_path.iterdir()) == []


def test_download_respects_size_limits(tmp_path):
    net = full_net("1.3.0")
    seen = []
    orig = net.__call__

    def spy(url, max_bytes):
        seen.append((url, max_bytes))
        return orig(url, max_bytes)
    rel = update.check_latest(spy)
    update.download_release(rel, tmp_path, spy)
    limits = dict(seen)
    assert limits[update.API_URL] == update.MAX_API_BYTES
    assert limits[ZIP_URL.format(v="1.3.0")] == update.MAX_ZIP_BYTES
    assert limits[SUMS_URL.format(v="1.3.0")] == update.MAX_SUMS_BYTES


# --- Archiv-Prüfung ----------------------------------------------------------------------

def _check(data, version="1.3.0"):
    return update.check_archive(io.BytesIO(data), version)


def test_valid_archive_lists_top_level_entries():
    assert _check(make_archive("1.3.0")) == ["README.md", "pyproject.toml", "src", "start.bat"]


@pytest.mark.parametrize("name", [
    "kassivo-1.3.0/../boese.txt", "kassivo-1.3.0/src/../../boese.txt", "/etc/boese",
    "C:/boese.txt", "kassivo-1.3.0/C:boese", "andere/datei.txt",
    "kassivo-1.3.0/./x", "kassivo-1.3.0//x","kassivo-1.3.0/x\ny",
    "kassivo-1.3.0/.git/config", "kassivo-1.3.0/.venv/x", "kassivo-1.3.0/.update/x",
    "kassivo-1.3.0/.env", "kassivo-1.3.0/data/kassivo.db", "kassivo-1.3.0/x.sqlite",
    "kassivo-1.3.0/kassivo.db-journal", "kassivo-1.3.0/kassivo.db-wal", "kassivo-1.3.0/X.DB",
    "kassivo-1.3.0/con.txt", "kassivo-1.3.0/x.", "kassivo-1.3.0/x:stream",
])
def test_archive_rejects_dangerous_names(name):
    with pytest.raises(update.UpdateError):
        _check(make_archive("1.3.0", {name: "x"}))


def test_backslash_in_name_is_rejected():
    """zipfile wandelt den Backslash unter Windows schon beim Lesen um; die eigene Prüfung
    greift auf jedem System."""
    with pytest.raises(update.UpdateError):
        update._check_parts("a\\b.txt")


def test_archive_rejects_wrong_prefix():
    with pytest.raises(update.UpdateError):
        _check(make_archive("1.3.0", prefix="kassivo-1.2.0/"))


def test_archive_rejects_case_duplicates():
    with pytest.raises(update.UpdateError):
        _check(make_archive("1.3.0", {"kassivo-1.3.0/readme.MD": "x"}))


def test_archive_rejects_symlink():
    buf = io.BytesIO(make_archive("1.3.0"))
    with zipfile.ZipFile(buf, "a") as z:
        info = zipfile.ZipInfo("kassivo-1.3.0/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "/etc/passwd")
    with pytest.raises(update.UpdateError):
        _check(buf.getvalue())


@pytest.mark.parametrize("skip", ["pyproject.toml", "src/kassivo/__init__.py"])
def test_archive_requires_core_files(skip):
    with pytest.raises(update.UpdateError):
        _check(make_archive("1.3.0", skip=(skip,)))


def test_archive_version_must_match():
    with pytest.raises(update.UpdateError):
        _check(make_archive("1.3.0", init_version="1.2.0"))
    with pytest.raises(update.UpdateError):
        _check(make_archive("1.3.0"), version="1.4.0")


def test_archive_rejects_garbage():
    with pytest.raises(update.UpdateError):
        _check(b"kein zip")


def test_archive_member_limit(monkeypatch):
    monkeypatch.setattr(update, "MAX_MEMBERS", 5)
    with pytest.raises(update.UpdateError):
        _check(make_archive("1.3.0", {f"kassivo-1.3.0/f{i}": "x" for i in range(3)}))


def test_archive_size_limits(monkeypatch):
    big = make_archive("1.3.0", {"kassivo-1.3.0/gross.bin": b"\0" * 5000})
    monkeypatch.setattr(update, "MAX_MEMBER_BYTES", 4000)
    with pytest.raises(update.UpdateError):
        _check(big)
    monkeypatch.setattr(update, "MAX_MEMBER_BYTES", 10_000)
    monkeypatch.setattr(update, "MAX_UNPACKED_BYTES", 5100)
    with pytest.raises(update.UpdateError):
        _check(big)


def test_zip_bomb_is_rejected_by_unpacked_limit(monkeypatch):
    monkeypatch.setattr(update, "MAX_UNPACKED_BYTES", 1_000_000)
    bomb = make_archive("1.3.0", {"kassivo-1.3.0/bombe.bin": b"\0" * 2_000_000})
    assert len(bomb) < 100_000
    with pytest.raises(update.UpdateError):
        _check(bomb)


def test_extract_writes_only_below_target(tmp_path):
    src = tmp_path / "kassivo-1.3.0.zip"
    src.write_bytes(make_archive("1.3.0"))
    root = update.extract_archive(src, "1.3.0", tmp_path / "neu")
    assert root == tmp_path / "neu" / "kassivo-1.3.0"
    assert (root / "src" / "kassivo" / "__init__.py").is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kassivo-1.3.0.zip", "neu"]


def test_extract_refuses_existing_target(tmp_path):
    src = tmp_path / "kassivo-1.3.0.zip"
    src.write_bytes(make_archive("1.3.0"))
    (tmp_path / "neu").mkdir()
    with pytest.raises(update.UpdateError):
        update.extract_archive(src, "1.3.0", tmp_path / "neu")


def test_extract_counts_real_bytes(tmp_path, monkeypatch):
    """Lügt das Archiv bei der Größe, zählt das Entpacken trotzdem mit."""
    src = tmp_path / "kassivo-1.3.0.zip"
    src.write_bytes(make_archive("1.3.0", {"kassivo-1.3.0/gross.bin": b"\0" * 5000}))
    monkeypatch.setattr(update, "check_archive", lambda f, v: ["src"])  # Vorprüfung umgehen
    monkeypatch.setattr(update, "MAX_UNPACKED_BYTES", 3000)
    with pytest.raises(update.UpdateError):
        update.extract_archive(src, "1.3.0", tmp_path / "neu")


# --- HTTP-Client -----------------------------------------------------------------------

class _Resp:
    def __init__(self, data):
        self._buf = io.BytesIO(data)

    def read(self, n=-1):
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Opener:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append((req, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return _Resp(self.result)


def test_fetch_sends_no_user_data():
    op = _Opener(b"{}")
    assert update.urllib_fetch(update.API_URL, 100, opener=op) == b"{}"
    req, timeout = op.requests[0]
    headers = {k.lower(): v for k, v in req.header_items()}
    assert headers["user-agent"] == "KASSIVO-Update"
    assert set(headers) <= {"user-agent", "accept", "x-github-api-version"}
    assert req.get_method() == "GET" and req.data is None
    assert timeout == update.TIMEOUT


def test_fetch_limits_size():
    with pytest.raises(update.UpdateError) as info:
        update.urllib_fetch(update.API_URL, 10, opener=_Opener(b"x" * 11))
    assert str(info.value) == update.E_TOO_BIG
    assert update.urllib_fetch(update.API_URL, 10, opener=_Opener(b"x" * 10)) == b"x" * 10


@pytest.mark.parametrize("exc,message", [
    (socket.timeout("t"), update.E_TIMEOUT),
    (TimeoutError(), update.E_TIMEOUT),
    (urllib.error.URLError(socket.timeout("t")), update.E_TIMEOUT),
    (urllib.error.URLError("offline"), update.E_NETWORK),
    (ConnectionResetError(), update.E_NETWORK),
    (ssl.SSLCertVerificationError("bad cert"), update.E_TLS),
    (urllib.error.URLError(ssl.SSLCertVerificationError("bad")), update.E_TLS),
])
def test_fetch_maps_errors(exc, message):
    with pytest.raises(update.UpdateError) as info:
        update.urllib_fetch(update.API_URL, 100, opener=_Opener(exc))
    assert str(info.value) == message


def test_fetch_http_error_has_status_only():
    err = urllib.error.HTTPError(update.API_URL, 403, "rate limit", {}, None)
    with pytest.raises(update.UpdateError) as info:
        update.urllib_fetch(update.API_URL, 100, opener=_Opener(err))
    assert "403" in str(info.value)


@pytest.mark.parametrize("url", ["http://api.github.com/x", "file:///etc/passwd", "ftp://x/y"])
def test_fetch_only_https(url):
    op = _Opener(b"x")
    with pytest.raises(update.UpdateError):
        update.urllib_fetch(url, 100, opener=op)
    assert op.requests == []


def test_tls_context_verifies_certificates():
    ctx = update._tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_redirect_to_http_is_refused():
    handler = update._HttpsOnlyRedirect()
    req = urllib.request.Request("https://github.com/x")
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://github.com/y")
    ok = handler.redirect_request(req, None, 302, "Found", {},
                                  "https://objects.githubusercontent.com/y")
    assert ok.full_url == "https://objects.githubusercontent.com/y"


def test_tests_never_reach_the_network():
    """Die Autouse-Fixture sperrt den echten Client in allen Tests."""
    with pytest.raises(RuntimeError):
        update.urllib_fetch(update.API_URL, 100)
