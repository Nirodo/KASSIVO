"""Update-Funktion (#31): neues GitHub-Release erkennen, Paket laden, prüfen, installieren.

Design: docs/superpowers/specs/2026-11-27-update-design.md.

- Nur lesend gegen die öffentliche Releases-API, ohne Token, ohne Nutzerdaten.
- Der HTTP-Client ist eine Funktion fetch(url, max_bytes) -> bytes und wird injiziert.
  Jeder Fehler des Clients ist ein UpdateError mit deutscher Meldung für die Oberfläche.
- Das Paket wird nur gespeichert, wenn Prüfsumme und Archiv-Prüfung passen.
- Installiert wird nicht im laufenden Programm, sondern mit update.bat (apply_update).
"""
import hashlib
import hmac
import http.client
import io
import json
import os
import re
import shutil
import socket
import ssl
import stat
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


REPO = "Nirodo/KASSIVO"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
DOWNLOAD_PREFIX = f"{RELEASES_PAGE}/download/"
USER_AGENT = "KASSIVO-Update"

TIMEOUT = 10  # Sekunden je Verbindungsschritt
TOTAL_DEADLINE = 120  # Sekunden für eine ganze Antwort (gegen tröpfelnde Server)
MAX_API_BYTES = 1024 * 1024
MAX_SUMS_BYTES = 64 * 1024
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_MEMBERS = 5000
MAX_MEMBER_BYTES = 50 * 1024 * 1024
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
MAX_NOTES = 5000
PORT = 8000

E_NETWORK = "GitHub ist nicht erreichbar. Bitte Internetverbindung oder Proxy prüfen."
E_TIMEOUT = "Zeitüberschreitung bei der Verbindung zu GitHub. Bitte später erneut versuchen."
E_TLS = "Die sichere Verbindung zu GitHub konnte nicht geprüft werden (Zertifikat)."
E_TOO_BIG = "Die Antwort von GitHub ist größer als erlaubt."
E_HTTPS = "Nur HTTPS-Verbindungen sind erlaubt."
E_ANSWER = "Die Antwort von GitHub ist ungültig."
E_NO_RELEASE = "Es wurde kein gültiges Release gefunden."
E_NO_PACKAGE = ("Für diese Version gibt es kein prüfbares Update-Paket"
                " (Archiv und SHA256SUMS fehlen).")
E_CHECKSUM = "Die Prüfsumme des Update-Pakets stimmt nicht. Es wurde nichts gespeichert."

_VERSION_RE = re.compile(r"v?(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})",
                         re.ASCII)
_SUMS_LINE_RE = re.compile(r"([0-9a-fA-F]{64}) [ *](\S+)", re.ASCII)
_DIGEST_RE = re.compile(r"sha256:([0-9a-f]{64})", re.ASCII)
_INIT_VERSION_RE = re.compile(r'^__version__ = "([^"]+)"$', re.M)
_ZIP_NAME_RE = re.compile(r"kassivo-(\d+\.\d+\.\d+)\.zip", re.ASCII)

FORBIDDEN_TOP = frozenset({".git", ".venv", ".update", ".env"})
FORBIDDEN_SUFFIXES = (".db", ".sqlite", "-journal", "-wal")
WINDOWS_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"}
                             | {f"COM{i}" for i in range(1, 10)}
                             | {f"LPT{i}" for i in range(1, 10)})


class UpdateError(Exception):
    """Fehler mit Meldung für Nutzer. Enthält nie Pfade, Tokens oder Antwortinhalte."""


# --- Versionen ---------------------------------------------------------------------------

def parse_version(text) -> tuple[int, int, int] | None:
    """Strenges SemVer X.Y.Z (optional mit v), ohne Vorabversion und führende Nullen."""
    if not isinstance(text, str):
        return None
    m = _VERSION_RE.fullmatch(text)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def is_newer(candidate, current) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


# --- HTTP-Client ---------------------------------------------------------------------------

def _tls_context() -> ssl.SSLContext:
    """Zertifikat und Hostname werden geprüft (unter Windows: Zertifikatsspeicher des Systems)."""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise urllib.error.HTTPError(newurl, code, "Weiterleitung ohne HTTPS", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_tls_context()), _HttpsOnlyRedirect())


def _is_timeout(exc) -> bool:
    return isinstance(exc, (TimeoutError, socket.timeout))


def urllib_fetch(url: str, max_bytes: int, *, opener=None, timeout: float = TIMEOUT) -> bytes:
    """GET ohne Anmeldung, nur HTTPS, mit Zeitlimit und Größengrenze."""
    if urlsplit(url).scheme != "https":
        raise UpdateError(E_HTTPS)
    opener = opener or _build_opener()
    accept = "application/vnd.github+json" if url == API_URL else "application/octet-stream"
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if url == API_URL:
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    req = urllib.request.Request(url, headers=headers, method="GET")
    deadline = time.monotonic() + TOTAL_DEADLINE
    try:
        with opener.open(req, timeout=timeout) as resp:
            chunks, size = [], 0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UpdateError(E_TOO_BIG)
                if time.monotonic() > deadline:
                    raise UpdateError(E_TIMEOUT)
                chunks.append(chunk)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"GitHub hat mit Fehler {int(exc.code)} geantwortet. "
                          "Bitte später erneut versuchen.") from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if _is_timeout(reason):
            raise UpdateError(E_TIMEOUT) from None
        if isinstance(reason, ssl.SSLError):
            raise UpdateError(E_TLS) from None
        raise UpdateError(E_NETWORK) from None
    except ssl.SSLError:
        raise UpdateError(E_TLS) from None
    except (TimeoutError, socket.timeout):
        raise UpdateError(E_TIMEOUT) from None
    except (OSError, http.client.HTTPException):
        raise UpdateError(E_NETWORK) from None
    return b"".join(chunks)


# --- Release abfragen ----------------------------------------------------------------------

@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    page_url: str
    zip_url: str | None
    sums_url: str | None
    zip_digest: str | None = None

    @property
    def has_package(self) -> bool:
        return bool(self.zip_url and self.sums_url)

    @property
    def zip_name(self) -> str:
        return f"kassivo-{self.version}.zip"


def _asset_url(assets, name: str, tag: str) -> tuple[str | None, dict | None]:
    """URL nur, wenn sie genau auf die Datei dieses Releases im festen Repository zeigt."""
    expected = f"{DOWNLOAD_PREFIX}{tag}/{name}"
    for a in assets:
        if isinstance(a, dict) and a.get("name") == name:
            return (expected, a) if a.get("browser_download_url") == expected else (None, None)
    return None, None


def check_latest(fetch) -> Release:
    """Neuestes Release (ohne Entwürfe und Vorabversionen). Wirft UpdateError."""
    raw = fetch(API_URL, MAX_API_BYTES)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise UpdateError(E_ANSWER) from None
    if not isinstance(data, dict):
        raise UpdateError(E_ANSWER)
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v") or parse_version(tag) is None:
        raise UpdateError(E_NO_RELEASE)
    if data.get("draft") or data.get("prerelease"):
        raise UpdateError(E_NO_RELEASE)
    version = tag[1:]
    notes = data.get("body") if isinstance(data.get("body"), str) else ""
    page = data.get("html_url")
    page_url = page if page == f"{RELEASES_PAGE}/tag/{tag}" else RELEASES_PAGE
    assets = data.get("assets") if isinstance(data.get("assets"), list) else []
    zip_url, zip_asset = _asset_url(assets, f"kassivo-{version}.zip", tag)
    sums_url, _ = _asset_url(assets, "SHA256SUMS", tag)
    digest = zip_asset.get("digest") if zip_asset else None
    return Release(version=version, notes=notes[:MAX_NOTES], page_url=page_url,
                   zip_url=zip_url, sums_url=sums_url,
                   zip_digest=digest if isinstance(digest, str) else None)


# --- Herunterladen und prüfen ----------------------------------------------------------------

def _expected_sum(sums: bytes, name: str) -> str:
    try:
        text = sums.decode("ascii")
    except UnicodeDecodeError:
        raise UpdateError(E_NO_PACKAGE) from None
    found = []
    for line in text.splitlines():
        m = _SUMS_LINE_RE.fullmatch(line.strip())
        if m and m[2] == name:
            found.append(m[1].lower())
    if len(found) != 1:
        raise UpdateError(E_NO_PACKAGE)
    return found[0]


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def download_release(release: Release, dest_dir: Path, fetch) -> Path:
    """Lädt Archiv und SHA256SUMS, prüft beides und speichert erst danach."""
    if not release.has_package:
        raise UpdateError(E_NO_PACKAGE)
    expected = _expected_sum(fetch(release.sums_url, MAX_SUMS_BYTES), release.zip_name)
    data = fetch(release.zip_url, MAX_ZIP_BYTES)
    actual = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise UpdateError(E_CHECKSUM)
    if release.zip_digest is not None:
        m = _DIGEST_RE.fullmatch(release.zip_digest)
        if not m or not hmac.compare_digest(actual, m[1]):
            raise UpdateError(E_CHECKSUM)
    check_archive(io.BytesIO(data), release.version)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / release.zip_name
    _write_atomic(target, data)
    _write_atomic(target.with_name(target.name + ".sha256"), (actual + "\n").encode("ascii"))
    return target


# --- Archiv-Prüfung --------------------------------------------------------------------------

def _bad(why: str) -> UpdateError:
    return UpdateError(f"Das Update-Paket ist ungültig: {why}.")


def _check_parts(rest: str) -> None:
    if "\\" in rest or ":" in rest or any(ord(c) < 32 or ord(c) == 127 for c in rest):
        raise _bad("unzulässiges Zeichen im Dateinamen")
    parts = rest.split("/")
    for part in parts:
        if part in ("", ".", "..") or part.endswith((".", " ")):
            raise _bad("unzulässiger Pfad")
        if part.split(".")[0].upper() in WINDOWS_RESERVED:
            raise _bad("reservierter Dateiname")
    if parts[0].casefold() in FORBIDDEN_TOP:
        raise _bad(f"geschützter Ordner {parts[0]}")
    if parts[-1].casefold().endswith(FORBIDDEN_SUFFIXES):
        raise _bad("Datenbankdatei im Paket")


def check_archive(fileobj, version: str) -> list[str]:
    """Prüft das Archiv ohne zu entpacken. Gibt die Einträge der obersten Ebene zurück."""
    prefix = f"kassivo-{version}/"
    try:
        z = zipfile.ZipFile(fileobj)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError):
        raise _bad("kein ZIP-Archiv") from None
    with z:
        infos = z.infolist()
        if len(infos) > MAX_MEMBERS:
            raise _bad("zu viele Dateien")
        seen, names, tops, total = set(), set(), set(), 0
        for info in infos:
            name = info.filename
            if not name.startswith(prefix):
                raise _bad("Datei außerhalb des Versionsordners")
            rest = name[len(prefix):]
            if info.is_dir():
                rest = rest[:-1]
                if not rest:
                    continue  # der Versionsordner selbst
            _check_parts(rest)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise _bad("symbolischer Link")
            key = rest.casefold()
            if key in seen:
                raise _bad("doppelter Dateiname")
            seen.add(key)
            names.add(rest)
            if info.file_size > MAX_MEMBER_BYTES:
                raise _bad("Datei zu groß")
            total += info.file_size
            if total > MAX_UNPACKED_BYTES:
                raise _bad("entpackt zu groß")
            tops.add(rest.split("/")[0])
        for required in ("pyproject.toml", "src/kassivo/__init__.py"):
            if required not in names:
                raise _bad(f"{required} fehlt")
        try:
            init = z.read(prefix + "src/kassivo/__init__.py").decode("utf-8")
        except (zipfile.BadZipFile, OSError, UnicodeDecodeError, EOFError):
            raise _bad("Versionsdatei nicht lesbar") from None
    m = _INIT_VERSION_RE.search(init)
    if not m or m[1] != version:
        raise _bad("Version im Paket passt nicht")
    return sorted(tops)


def extract_archive(zip_path: Path, version: str, dest: Path) -> Path:
    """Entpackt in einen neuen, leeren Ordner. Zählt die echten Bytes mit."""
    dest = Path(dest)
    with open(zip_path, "rb") as f:
        check_archive(f, version)
    try:
        dest.mkdir(parents=True)
    except FileExistsError:
        raise _bad("Zielordner existiert bereits") from None
    root = dest.resolve()
    total = 0
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                target = dest / info.filename
                resolved = target.resolve()
                if resolved != root and root not in resolved.parents:
                    raise _bad("Pfad außerhalb des Zielordners")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with z.open(info) as src, open(target, "xb") as out:
                    while chunk := src.read(64 * 1024):
                        written += len(chunk)
                        total += len(chunk)
                        if written > MAX_MEMBER_BYTES:
                            raise _bad("Datei zu groß")
                        if total > MAX_UNPACKED_BYTES:
                            raise _bad("entpackt zu groß")
                        out.write(chunk)
    except BaseException as exc:
        shutil.rmtree(dest, ignore_errors=True)  # nur der eben angelegte Ordner
        if isinstance(exc, (zipfile.BadZipFile, EOFError, OSError)) and not isinstance(
                exc, UpdateError):
            raise _bad("Archiv beschädigt") from None
        raise
    return dest / f"kassivo-{version}"
