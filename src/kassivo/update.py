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
import sqlite3
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

# Alles, was apply_update braucht, wird hier oben importiert: Während des Austauschs liegt
# der alte Programmordner schon woanders, späte Importe kämen aus dem neuen Stand.
from kassivo import __version__, backup, settings, update_recover

REPO = "Nirodo/KASSIVO"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
DOWNLOAD_PREFIX = f"{RELEASES_PAGE}/download/"
USER_AGENT = "KASSIVO-Update"
# Nur diese Hosts, auch bei Weiterleitungen (Release-Dateien liegen hinter Weiterleitungen)
ALLOWED_HOSTS = frozenset({"api.github.com", "github.com", "objects.githubusercontent.com",
                           "release-assets.githubusercontent.com"})
SETTING_KEY = "update_check"  # Tabelle settings: "1" = Update-Prüfung erlaubt, Standard aus

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
E_HTTPS = "Nur HTTPS-Verbindungen zu GitHub sind erlaubt."
E_ANSWER = "Die Antwort von GitHub ist ungültig."
E_NO_RELEASE = "Es wurde kein gültiges Release gefunden."
E_NO_PACKAGE = ("Für diese Version gibt es kein prüfbares Update-Paket"
                " (Archiv und SHA256SUMS fehlen).")
E_CHECKSUM = "Die Prüfsumme des Update-Pakets stimmt nicht. Es wurde nichts gespeichert."

_VERSION_RE = re.compile(r"v?(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})",
                         re.ASCII)
_SUMS_LINE_RE = re.compile(r"([0-9a-fA-F]{64}) [ *](\S+)", re.ASCII)
_DIGEST_RE = re.compile(r"sha256:([0-9a-f]{64})", re.ASCII)
_INIT_VERSION_RE = re.compile(r'^__version__ = "([^"]+)"\s*$', re.M)  # auch CRLF
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


def _allowed_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "") in ALLOWED_HOSTS and (
        parts.port in (None, 443))


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_url(newurl):
            raise urllib.error.HTTPError(newurl, code, "Weiterleitung abgelehnt", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_tls_context()), _HttpsOnlyRedirect())


def _is_timeout(exc) -> bool:
    return isinstance(exc, (TimeoutError, socket.timeout))


def urllib_fetch(url: str, max_bytes: int, *, opener=None, timeout: float = TIMEOUT) -> bytes:
    """GET ohne Anmeldung, nur HTTPS, mit Zeitlimit und Größengrenze."""
    if not _allowed_url(url):
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
    # "~" wegen 8.3-Kurznamen unter Windows (KASSIV~1 könnte einen anderen Ordner treffen)
    if "\\" in rest or ":" in rest or "~" in rest or any(ord(c) < 32 or ord(c) == 127 for c in rest):
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


def extract_archive(source, version: str, dest: Path, *, strip: bool = False) -> Path:
    """Entpackt in einen neuen, leeren Ordner. Zählt die echten Bytes mit.

    source: Pfad oder offene Datei. Mit offener Datei prüft und entpackt dieselbe Datei, die
    vorher gehasht wurde (kein Austausch zwischen Prüfung und Entpacken).
    strip: ohne den Versionsordner kassivo-X.Y.Z/ entpacken (kürzere Pfade, MAX_PATH)."""
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as f:
            return extract_archive(f, version, dest, strip=strip)
    prefix = f"kassivo-{version}/"
    dest = Path(dest)
    source.seek(0)
    check_archive(source, version)
    source.seek(0)
    zip_path = source
    try:
        dest.mkdir(parents=True)
    except FileExistsError:
        raise _bad("Zielordner existiert bereits") from None
    root = dest.resolve()
    total = 0
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                name = info.filename
                if strip:
                    name = name[len(prefix):] if name.startswith(prefix) else name
                    if not name:
                        continue
                target = dest / name
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
    return dest if strip else dest / f"kassivo-{version}"


# --- Installieren (aus update.bat, KASSIVO ist beendet) ------------------------------------
#
# Austausch Datei für Datei (nicht ordnerweise): Dateien, die nicht zum Paket gehören (eigene
# Notizen, .claude/settings.local.json, docs/design/referenz), bleiben, wo sie sind. Entfernt
# werden nur Dateien, die im vorigen Paket standen (.update/dateien.txt) und im neuen fehlen.
# Vor dem ersten Verschieben steht der Plan im Journal (update_recover). Bricht der Lauf hart
# ab, setzt update.bat beim nächsten Start mit .update/wiederherstellen.py zurück, auch wenn
# src/kassivo dann nicht importierbar ist. Kurze Ordnernamen (a-, n-, f-) wegen MAX_PATH.

E_RUNNING = ("KASSIVO läuft noch (Port 8000 ist belegt). Bitte KASSIVO beenden"
             " (Fenster schließen) und update.bat erneut starten.")
E_NOT_EDITABLE = ("KASSIVO ist nicht als editierbare Installation aus diesem Projektordner"
                  " installiert. Das Update braucht die Installation mit"
                  " „pip install -e .“ (siehe README). Bitte von Hand aktualisieren.")
E_SUM_FILE = "Die Prüfsumme des Update-Pakets fehlt oder stimmt nicht. Bitte erneut herunterladen."
E_INSIDE = ("Datenbank, Sicherungen oder Update-Paket liegen im Projektordner. Update"
            " abgebrochen, es wurde nichts geändert.")
E_PYPROJECT = "pyproject.toml ist nicht lesbar. Update abgebrochen, es wurde nichts geändert."
E_UNFINISHED = ("Ein früheres Update war unvollständig. Der alte Stand wurde"
                " zurückgesetzt. Bitte update.bat erneut starten.")
E_CLOUD_HINT = ("Hinweis: Der Projektordner liegt in einem Cloud-Ordner. Bitte die"
                " OneDrive-Synchronisation pausieren, bis das Update fertig ist; gesperrte"
                " Dateien versucht das Update mehrmals.")
SMOKE = "import kassivo, kassivo.web.app; print(kassivo.__version__)"
MANIFEST = "dateien.txt"
_DEPS_RE = re.compile(r"^dependencies\s*=\s*\[(.*?)\]", re.S | re.M)
WORK = update_recover.WORK


@dataclass(frozen=True)
class ApplyResult:
    version: str
    backup_name: str | None
    rollback_dir: Path


def port_in_use(port: int = PORT) -> bool:
    """Läuft KASSIVO noch? Nur ein Verbindungsversuch auf 127.0.0.1, es wird nichts gesendet."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _sidecar(zip_path: Path) -> Path:
    return zip_path.with_name(zip_path.name + ".sha256")


def _verify_handle(f, zip_path: Path) -> None:
    """Prüfsumme der offenen Datei gegen die .sha256-Datei (dieselbe Datei wird entpackt)."""
    try:
        expected = _sidecar(zip_path).read_text("ascii").strip().lower()
        f.seek(0)
        digest = hashlib.sha256()
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    except (OSError, UnicodeDecodeError):
        raise UpdateError(E_SUM_FILE) from None
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or not hmac.compare_digest(
            digest.hexdigest(), expected):
        raise UpdateError(E_SUM_FILE)


def find_package(update_dir, current: str) -> Path | None:
    """Neuestes heruntergeladenes Paket mit Prüfsummen-Datei, das neuer ist als current."""
    update_dir = Path(update_dir)
    if not update_dir.is_dir():
        return None
    found = []
    for p in update_dir.iterdir():
        m = _ZIP_NAME_RE.fullmatch(p.name)
        if m and is_newer(m[1], current) and p.is_file() and _sidecar(p).is_file():
            found.append((parse_version(m[1]), p))
    return max(found)[1] if found else None


def _dependencies(pyproject: Path) -> list[str]:
    try:
        text = pyproject.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        raise UpdateError(E_PYPROJECT) from None
    m = _DEPS_RE.search(text)
    return sorted(re.findall(r'"([^"]+)"', m[1])) if m else []


def _inside(path: Path, folder: Path) -> bool:
    path, folder = Path(path).resolve(), Path(folder).resolve()
    return path == folder or folder in path.parents


def _backup_database(db_path: Path) -> str | None:
    """Kopie über die SQLite-Backup-API; die Datenbank wird nur lesend geöffnet."""
    db_path = Path(db_path)
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            return backup.backup_db(conn, settings.backup_dir(db_path)).name
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        raise UpdateError("Die Sicherung der Datenbank ist fehlgeschlagen. Das Update wurde"
                          f" abgebrochen, es wurde nichts geändert ({type(exc).__name__}).") from None


class _Log:
    """Protokoll ohne personenbezogene Daten und ohne volle Pfade: Zeit, Versionen, Schritte."""

    def __init__(self, path: Path, out):
        self.path, self.out = path, out

    def __call__(self, text: str) -> None:
        self.out(text)
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
        except OSError:
            pass  # ein fehlendes Protokoll darf das Update nicht stoppen


def preflight(project, zip_path, db_path, *, current: str = __version__,
              port_in_use=port_in_use, package_dir=None) -> str:
    """Alle Prüfungen ohne Änderung (vor der j/n-Frage). Gibt die Zielversion zurück."""
    project, zip_path, db_path = Path(project), Path(zip_path), Path(db_path)
    m = _ZIP_NAME_RE.fullmatch(zip_path.name)
    if not m or not is_newer(m[1], current):
        raise UpdateError(f"Das Paket ist nicht neuer als die installierte Version {current}.")
    package_dir = Path(package_dir) if package_dir is not None else Path(__file__).parent
    if not (project / "pyproject.toml").is_file() or not _inside(
            package_dir, project / "src" / "kassivo") or not _inside(
            project / "src" / "kassivo", package_dir):
        raise UpdateError(E_NOT_EDITABLE)
    if port_in_use():
        raise UpdateError(E_RUNNING)
    for local in (db_path, zip_path, settings.backup_dir(db_path)):
        if _inside(local, project):
            raise UpdateError(E_INSIDE)
    with open(zip_path, "rb") as f:
        _verify_handle(f, zip_path)
        check_archive(f, m[1])
    _dependencies(project / "pyproject.toml")
    return m[1]


def _package_files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


_MANIFEST_LINE_RE = re.compile(r"([0-9a-f]{64})  (.+)")


def _old_manifest(project: Path) -> dict[str, str]:
    """Dateiliste des vorigen Pakets: Pfad -> SHA-256. Unzulässige Zeilen zählen nicht."""
    try:
        lines = (project / WORK / MANIFEST).read_text("utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    out = {}
    for line in lines:
        m = _MANIFEST_LINE_RE.fullmatch(line)
        if m and update_recover.safe_rel(project, m[2]):
            out[m[2]] = m[1]
    return out


def _stale_files(project: Path, new_files: list[str]) -> list[str]:
    """Veraltet ist nur eine Datei, die im vorigen Paket stand, im neuen fehlt (auch nicht in
    anderer Groß-/Kleinschreibung) und noch genau den Inhalt aus dem vorigen Paket hat.

    So bleibt jede eigene oder geänderte Datei, selbst wenn jemand dateien.txt manipuliert:
    ein Eintrag wirkt nur auf eine Datei, deren Inhalt mit dem eingetragenen Hash
    übereinstimmt, also eine unveränderte Programmdatei. Geschützte Ordner und Pfade außerhalb
    des Projekts filtert safe_rel. Verschoben wird ohnehin nur in den alt-Ordner, nie gelöscht."""
    new_keys = {rel.casefold() for rel in new_files}
    stale = []
    for rel, digest in sorted(_old_manifest(project).items()):
        path = project / rel
        if rel.casefold() in new_keys or not path.is_file() or path.is_symlink():
            continue
        try:
            if hmac.compare_digest(_file_hash(path), digest):
                stale.append(rel)
        except OSError:
            continue
    return stale


def _freeze(run, python, project, work: Path, tag: str) -> str:
    """pip-Stand vor dem Update sichern (ohne kassivo selbst), für den Rückweg."""
    r = run([python, "-m", "pip", "freeze"], cwd=project, capture_output=True, text=True,
            timeout=120)
    if r.returncode != 0:
        raise UpdateError("pip freeze ist fehlgeschlagen. Update abgebrochen.")
    keep = [ln for ln in r.stdout.splitlines()
            if ln.strip() and not ln.startswith("-e") and not ln.lower().startswith("kassivo")]
    name = f"pip-{tag}.txt"
    (work / name).write_text("\n".join(keep) + "\n", "utf-8")
    return name


def apply_update(project_dir, zip_path, db_path, *, current: str = __version__,
                 port_in_use=port_in_use, run=subprocess.run, python: str = sys.executable,
                 package_dir=None, now: datetime | None = None, out=lambda s: None) -> ApplyResult:
    """Installiert ein heruntergeladenes, geprüftes Paket. Ablauf siehe Design R4."""
    project, zip_path, db_path = Path(project_dir), Path(zip_path), Path(db_path)
    work = project / WORK
    try:
        if update_recover.read_journal(project) is not None:
            update_recover.recover(project, run=run, python=python)
            raise UpdateError(E_UNFINISHED)
    except update_recover.JournalError as exc:
        raise UpdateError(str(exc)) from None
    version = preflight(project, zip_path, db_path, current=current, port_in_use=port_in_use,
                        package_dir=package_dir)
    log = _Log(zip_path.parent / "update.log", out)
    log(f"Update {current} -> {version} gestartet.")
    backup_name = _backup_database(db_path)
    log(f"Datenbank gesichert: {backup_name}." if backup_name else "Keine Datenbank vorhanden.")

    tag = f"{(now or datetime.now()):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    names = {"alt": f"a-{tag}", "neu": f"n-{tag}", "fehl": f"f-{tag}"}
    work.mkdir(exist_ok=True)
    with open(zip_path, "rb") as f:  # ein Handle: gehasht, geprüft und entpackt wird dasselbe
        _verify_handle(f, zip_path)
        neu = extract_archive(f, version, work / names["neu"], strip=True)
    new_files = _package_files(neu)
    manifest = "".join(f"{_file_hash(neu / rel)}  {rel}\n" for rel in new_files)
    plan = [[rel, (project / rel).is_file()] for rel in new_files]
    plan += [[rel, True] for rel in _stale_files(project, new_files)]
    deps_changed = _dependencies(project / "pyproject.toml") != _dependencies(
        neu / "pyproject.toml")
    shutil.copyfile(Path(update_recover.__file__), work / "wiederherstellen.py")
    journal = {"current": current, "version": version, "files": plan, "pip_ran": False,
               "freeze": None, **names}
    if deps_changed:
        journal["freeze"] = _freeze(run, python, project, work, tag)
    update_recover.write_journal(work, journal)  # ab hier ist jeder Schritt zurücksetzbar

    alt, ok, rolled_back = work / names["alt"], False, False
    try:
        for rel, had_old in plan:
            if had_old:
                update_recover.move(project / rel, alt / rel)
            if (neu / rel).exists():
                update_recover.move(neu / rel, project / rel)
        log("Programmdateien ausgetauscht.")
        if deps_changed:
            journal["pip_ran"] = True
            update_recover.write_journal(work, journal)
            log("Abhängigkeiten haben sich geändert: pip install -e . läuft.")
            r = run([python, "-m", "pip", "install", "-e", "."], cwd=project,
                    capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                raise UpdateError("pip install -e . ist fehlgeschlagen (PyPI erreichbar?).")
        r = run([python, "-I", "-c", SMOKE], cwd=project, capture_output=True, text=True,
                timeout=120)
        if r.returncode != 0 or r.stdout.strip() != version:
            raise UpdateError("Die neue Version startet nicht.")
        ok = True
    except (UpdateError, OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, UpdateError) else type(exc).__name__ + "."
        rolled_back = True  # auch wenn der Rückweg scheitert: nicht noch einmal im finally
        notes = _rollback(project, run, python, log, reason)
        raise UpdateError(f"Update fehlgeschlagen: {reason} Der alte Stand wurde"
                          f" zurückgesetzt. {' '.join(notes)}".strip()) from None
    finally:
        if not ok and not rolled_back:
            # BaseException (Strg+C, SystemExit): zurücksetzen und danach weiterwerfen
            _rollback(project, run, python, log, "Abbruch")
    # Erst das Journal weg (Update gilt als fertig), dann die Liste: bricht es dazwischen ab,
    # fehlt nur die Liste, und das nächste Update räumt eben keine veralteten Dateien weg.
    (work / update_recover.JOURNAL).unlink()
    (work / MANIFEST).write_text(manifest, "utf-8")
    shutil.rmtree(neu, ignore_errors=True)  # nur der eigene, jetzt leere Entpackordner
    log(f"Update auf {version} abgeschlossen. Alter Stand: {WORK}/{names['alt']}")
    return ApplyResult(version=version, backup_name=backup_name, rollback_dir=alt)


def _rollback(project: Path, run, python, log, reason: str) -> list[str]:
    try:
        notes = update_recover.recover(project, run=run, python=python)
    except (OSError, update_recover.JournalError):
        log("Zurücksetzen fehlgeschlagen.")
        raise UpdateError(
            f"Update fehlgeschlagen ({reason}) und das Zurücksetzen auch. Bitte update.bat"
            " erneut starten; es setzt laut .update/journal.json zurück. Hilft das nicht,"
            " siehe Wiki „Update“, Wiederherstellung von Hand.") from None
    log(f"Update fehlgeschlagen, alter Stand zurückgesetzt: {reason}")
    return [n for n in notes if "Python-Pakete" in n]


def main(argv=None, *, db_path=None, project_dir=None, package_dir=None,
         current: str = __version__, ask=input, out=print, **apply_kw) -> int:
    """python -I -m kassivo.update apply. Rückgabe 0 = installiert, 1 = nichts getan, 2 = Fehler."""
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["apply"]:
        out("Aufruf: python -I -m kassivo.update apply")
        return 2
    db_path = Path(db_path) if db_path is not None else settings.default_db_path()
    project = Path(project_dir) if project_dir is not None else Path(__file__).resolve().parents[2]
    try:
        unfinished = update_recover.read_journal(project) is not None
        if unfinished:
            update_recover.recover(project, run=apply_kw.get("run", subprocess.run),
                                   python=apply_kw.get("python", sys.executable))
    except update_recover.JournalError as exc:
        out(f"Fehler: {exc}")
        return 2
    except OSError:
        out("Fehler: Ein früheres Update ist unvollständig und ließ sich nicht"
            " zurücksetzen. Siehe Wiki „Update“, Wiederherstellung von Hand.")
        return 2
    if unfinished:
        out(f"Fehler: {E_UNFINISHED}")
        return 2
    package = find_package(settings.update_dir(db_path), current)
    if package is None:
        out("Kein heruntergeladenes Update gefunden. Bitte zuerst in KASSIVO unter"
            " Einstellungen, Update das Update herunterladen.")
        return 1
    try:
        version = preflight(project, package, db_path, current=current,
                            port_in_use=apply_kw.get("port_in_use", port_in_use),
                            package_dir=package_dir)
    except UpdateError as exc:
        out(f"Fehler: {exc}")
        return 2
    out(f"Installiert: {current}. Heruntergeladen und geprüft: {version}.")
    out("Vorher wird die Datenbank gesichert. Die Datenbank selbst wird nicht verändert.")
    if settings.cloud_warning(project) is not None:
        out(E_CLOUD_HINT)
    try:
        answer = ask(f"Update auf {version} jetzt installieren? (j/n) ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer.strip().lower() not in ("j", "ja"):
        out("Update abgebrochen. Es wurde nichts geändert.")
        return 1
    try:
        apply_update(project, package, db_path, current=current, package_dir=package_dir,
                     out=out, **apply_kw)
    except UpdateError as exc:
        out(f"Fehler: {exc}")
        return 2
    except KeyboardInterrupt:
        out("Abgebrochen (Strg+C). Der alte Stand wurde zurückgesetzt.")
        return 2
    out(f"KASSIVO {version} ist installiert. Bitte jetzt start.bat starten.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
