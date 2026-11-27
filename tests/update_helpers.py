"""Hilfen für Update-Tests: erfundene Release-Antworten und Archive. Keine Netzaufrufe."""
import hashlib
import io
import json
import zipfile

from kassivo import update

ZIP_URL = update.DOWNLOAD_PREFIX + "v{v}/kassivo-{v}.zip"
SUMS_URL = update.DOWNLOAD_PREFIX + "v{v}/SHA256SUMS"


def make_archive(version="1.3.0", extra=None, *, prefix=None, init_version=None,
                 skip=()) -> bytes:
    """Archiv wie aus git archive: alles unter kassivo-X.Y.Z/."""
    prefix = f"kassivo-{version}/" if prefix is None else prefix
    files = {
        "pyproject.toml": f'[project]\nname = "kassivo"\nversion = "{version}"\n'
                          'dependencies = ["fastapi>=0.110"]\n',
        "src/kassivo/__init__.py": f'"""KASSIVO."""\n\n__version__ = "{init_version or version}"\n',
        "start.bat": "@echo off\r\n",
        "README.md": "# KASSIVO\n",
    }
    files = {k: v for k, v in files.items() if k not in skip}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(prefix + name, data)
        for name, data in (extra or {}).items():
            info = zipfile.ZipInfo("platzhalter")
            info.filename = name  # unverändert, ohne Normalisierung durch zipfile
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def release_json(version="1.3.0", *, assets=None, digest=None, **over) -> bytes:
    if assets is None:
        zip_asset = {"name": f"kassivo-{version}.zip",
                     "browser_download_url": ZIP_URL.format(v=version)}
        if digest:
            zip_asset["digest"] = digest
        assets = [zip_asset, {"name": "SHA256SUMS",
                              "browser_download_url": SUMS_URL.format(v=version)}]
    body = {"tag_name": f"v{version}", "name": f"KASSIVO {version}", "draft": False,
            "prerelease": False, "body": "Neu: Update-Funktion.",
            "html_url": f"https://github.com/Nirodo/KASSIVO/releases/tag/v{version}",
            "assets": assets}
    body.update(over)
    return json.dumps(body).encode()


class FakeNet:
    """Ersetzt den HTTP-Client: feste Antworten je URL, merkt sich jeden Aufruf."""

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def __call__(self, url, max_bytes):
        self.calls.append(url)
        value = self.responses.get(url)
        if value is None:
            raise update.UpdateError(update.E_NETWORK)
        if isinstance(value, Exception):
            raise value
        if len(value) > max_bytes:
            raise update.UpdateError(update.E_TOO_BIG)
        return value


def full_net(version="1.3.0", archive=None, sums=None, **release_kw) -> FakeNet:
    archive = make_archive(version) if archive is None else archive
    if sums is None:
        sums = f"{sha(archive)}  kassivo-{version}.zip\n".encode()
    return FakeNet({
        update.API_URL: release_json(version, **release_kw),
        ZIP_URL.format(v=version): archive,
        SUMS_URL.format(v=version): sums,
    })
