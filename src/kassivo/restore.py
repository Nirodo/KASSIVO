"""Sicherung für einen anderen PC: Export als .kassivo-Datei, Prüfen und Einspielen (#32).

Eine .kassivo-Datei ist ein ZIP mit genau zwei Einträgen: manifest.json (Programmversion,
Schema-Version, Erstellzeit, SHA-256 und Größe der Datenbank) und kassivo.db (konsistente
Kopie über die SQLite-Backup-API). Nicht verschlüsselt: sie enthält Klarnamen und IBANs.

Einspielen in zwei Schritten:
  1. stage_upload / stage_local: Datei prüfen und als <token>.db in den Bereitstellungsordner
     neben der Datenbank legen. Die aktuelle Datenbank bleibt unberührt.
  2. restore: bereitgestellte Datei erneut prüfen, ältere Schema-Version mit der bestehenden
     Migration umstellen (mit Sicherung), Sicherheitskopie der aktuellen Datenbank anlegen,
     dann atomar per os.replace austauschen.

Verworfen:
  - ZIP entpacken (extractall): Namen aus dem Archiv werden nie als Pfad benutzt, es gibt nur
    die zwei festen Einträge; so ist Zip-Slip ausgeschlossen.
  - Erst ersetzen, dann migrieren: Scheitert die Migration, wäre die aktive Datenbank halb
    umgestellt. Migriert wird deshalb die bereitgestellte Kopie.
  - Rechnername im Manifest: wird nicht gebraucht und gibt unnötig etwas über den PC preis.
  - Verschlüsselung mit Passwort: nicht in v1 (offener Punkt im Issue).
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kassivo import __version__, backup, db

FORMAT = "kassivo-backup"
FORMAT_VERSION = 1
MANIFEST = "manifest.json"
DB_ENTRY = "kassivo.db"
EXPECTED_ENTRIES = sorted([MANIFEST, DB_ENTRY])
MAX_MANIFEST_BYTES = 64 * 1024
MAX_DB_BYTES = 512 * 1024 * 1024
CHUNK = 1024 * 1024
SQLITE_MAGIC = b"SQLite format 3\x00"
TOKEN_RE = re.compile(r"[0-9a-f]{32}", re.ASCII)
_STAGED_RE = re.compile(r"\.?[0-9a-f]{32}\.(db|part)", re.ASCII)
_VERSION_RE = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", re.ASCII)
_SHA_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_DATE_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", re.ASCII)
# Tabellen der Ausgangsfassung (Schema 1); jede KASSIVO-Datenbank hat sie.
REQUIRED_TABLES = frozenset({
    "account", "membership_type", "fee_rate", "member", "member_type_history", "member_iban",
    "category", "category_rule", "import_batch", "transaction", "allocation", "settings"})

E_ZIP = "Die Datei ist keine gültige KASSIVO-Sicherung (beschädigtes oder fremdes ZIP-Archiv)."
E_ENTRIES = ("Die Sicherung enthält unerwartete Einträge. Erwartet werden genau "
             "manifest.json und kassivo.db.")
E_MANIFEST = "Die Beschreibung der Sicherung (manifest.json) fehlt, ist ungültig oder passt nicht."
E_CHECKSUM = "Die Prüfsumme stimmt nicht. Die Sicherung ist beschädigt oder wurde verändert."
E_TOO_BIG = (f"Die Datenbank in der Sicherung ist zu groß "
             f"(Grenze {MAX_DB_BYTES // (1024 * 1024)} MB).")
E_NOT_DB = "Die Sicherung enthält keine gültige SQLite-Datenbank."
E_CORRUPT = "Die Datenbank in der Sicherung ist beschädigt (Integritätsprüfung fehlgeschlagen)."
E_SCHEMA = "Die Datenbank in der Sicherung hat nicht den Aufbau einer KASSIVO-Datenbank."
E_EXPIRED = "Die Vorschau ist nicht mehr gültig. Bitte die Sicherung erneut auswählen."
E_BUSY = ("Die aktuelle Datenbank ist gerade von einem anderen Programm oder einer zweiten "
          "KASSIVO-Instanz geöffnet. Es wurde nichts ersetzt.")
E_SAFETY = ("Die Sicherheitskopie der aktuellen Datenbank ist fehlgeschlagen. "
            "Es wurde nichts ersetzt.")
E_REPLACE = ("Die Datenbank konnte nicht ersetzt werden, vermutlich ist sie von einem anderen "
             "Programm geöffnet. Die bisherige Datenbank ist unverändert. "
             "Sicherheitskopie: {name}.")


class RestoreError(Exception):
    """Sicherung kann nicht eingespielt werden; die Meldung ist für Nutzer gedacht."""


@dataclass
class Staged:
    token: str
    path: Path
    schema: int
    summary: dict
    created: str          # Anzeige, TT.MM.JJJJ HH:MM
    app_version: str | None
    source: str | None = None  # Dateiname bei einer Sicherung aus backups/


@dataclass
class Restored:
    safety: str | None    # Dateiname der Sicherheitskopie in backups/
    members: int
    transactions: int


# --- Export ---

def export_name(now: datetime | None = None) -> str:
    return "kassivo-backup-" + (now or datetime.now()).strftime("%Y%m%d-%H%M%S") + ".kassivo"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _open(path: Path) -> sqlite3.Connection:
    # Nur eigene Kopien werden so geöffnet, nie die Datei des Nutzers selbst.
    return sqlite3.connect(str(path), timeout=1.0)


def summary(conn: sqlite3.Connection) -> dict:
    """Eckdaten für Manifest und Vorschau (keine Namen, keine IBANs)."""
    members = conn.execute("select count(*) from member").fetchall()[0][0]
    tx, last = conn.execute('select count(*), max(booking_date) from "transaction"').fetchall()[0]
    return {"members": members, "transactions": tx, "last_booking": last,
            "schema": db.schema_version(conn)}


def export_backup(conn: sqlite3.Connection, out: Path, now: datetime | None = None) -> dict:
    """Schreibt die .kassivo-Datei nach out und gibt das Manifest zurück."""
    out = Path(out)
    snap = out.with_name(out.name + ".snap")
    try:
        backup._copy(conn, snap)
        c = _open(snap)
        try:
            info = summary(c)
        finally:
            c.close()
        manifest = {
            "format": FORMAT, "format_version": FORMAT_VERSION, "app_version": __version__,
            "schema_version": info["schema"],
            "created_at": (now or datetime.now()).replace(microsecond=0).isoformat(),
            "db_sha256": _sha256(snap), "db_size": snap.stat().st_size,
            "members": info["members"], "transactions": info["transactions"],
        }
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(MANIFEST, json.dumps(manifest, indent=2))
            z.write(snap, DB_ENTRY)
    finally:
        snap.unlink(missing_ok=True)
    return manifest


# --- Prüfen ---

def _int(v, lo=0, hi=2**62) -> bool:
    return type(v) is int and lo <= v <= hi


def _parse_manifest(raw: bytes) -> dict:
    try:
        m = json.loads(raw)
    except ValueError as exc:
        raise RestoreError(E_MANIFEST) from exc
    if not isinstance(m, dict):
        raise RestoreError(E_MANIFEST)
    created = m.get("created_at")
    ok = (m.get("format") == FORMAT
          and type(m.get("format_version")) is int and m["format_version"] == FORMAT_VERSION
          and isinstance(m.get("app_version"), str) and _VERSION_RE.fullmatch(m["app_version"])
          and _int(m.get("schema_version"), 0, 10**6)
          and isinstance(created, str) and len(created) <= 40
          and isinstance(m.get("db_sha256"), str) and _SHA_RE.fullmatch(m["db_sha256"])
          and _int(m.get("db_size")))
    if not ok:
        raise RestoreError(E_MANIFEST)
    try:
        m["_created"] = datetime.fromisoformat(created)
    except ValueError as exc:
        raise RestoreError(E_MANIFEST) from exc
    return m


def check_db(path: Path) -> dict:
    """Prüft eine bereitgestellte Kopie. Gibt summary() zurück.

    Fehler: RestoreError, SchemaTooNewError (aus einer neueren KASSIVO-Version)."""
    with open(path, "rb") as f:
        if f.read(len(SQLITE_MAGIC)) != SQLITE_MAGIC:
            raise RestoreError(E_NOT_DB)
    try:
        c = _open(path)
    except sqlite3.DatabaseError as exc:
        raise RestoreError(E_NOT_DB) from exc
    try:
        try:
            version = db.schema_version(c)
            target = db.current_schema_version(db.MIGRATIONS)
            if version > target:
                raise db._too_new(version, target)
            if c.execute("pragma integrity_check").fetchall() != [("ok",)]:
                raise RestoreError(E_CORRUPT)
            kinds = c.execute("select type, name from sqlite_master").fetchall()
            tables = {n for t, n in kinds if t == "table"}
            # KASSIVO legt nie Trigger oder Views an. Fremde Trigger würden bei der Migration
            # oder später beim Schreiben laufen: ablehnen.
            if not REQUIRED_TABLES <= tables or any(t in ("trigger", "view") for t, _ in kinds):
                raise RestoreError(E_SCHEMA)
            return summary(c)
        except sqlite3.DatabaseError as exc:
            raise RestoreError(E_CORRUPT) from exc
    finally:
        c.close()


# --- Bereitstellen ---

def clear_staging(staging: Path) -> None:
    """Entfernt frühere Bereitstellungen (nur eigene Dateinamen, keine Links)."""
    if not staging.is_dir():
        return
    for p in staging.iterdir():
        if not _STAGED_RE.fullmatch(p.name):
            continue
        try:
            if stat.S_ISREG(p.lstat().st_mode):
                p.unlink()
        except OSError:
            pass


def _new_slot(staging: Path) -> tuple[str, Path, Path]:
    staging.mkdir(parents=True, exist_ok=True)
    clear_staging(staging)
    token = secrets.token_hex(16)
    return token, staging / f".{token}.part", staging / f"{token}.db"


def _extract_db(z: zipfile.ZipFile, info: zipfile.ZipInfo, part: Path) -> tuple[str, int]:
    """Kopiert den Datenbank-Eintrag nach part, zählt mit und bricht an der Grenze ab."""
    h, total = hashlib.sha256(), 0
    with z.open(info) as src, open(part, "wb") as out:
        while chunk := src.read(CHUNK):
            total += len(chunk)
            if total > MAX_DB_BYTES:
                raise RestoreError(E_TOO_BIG)
            h.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    return h.hexdigest(), total


def _read_zip(path: Path, part: Path) -> dict:
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise RestoreError(E_ZIP) from exc
    with z:
        infos = z.infolist()
        if sorted(i.filename for i in infos) != EXPECTED_ENTRIES:
            raise RestoreError(E_ENTRIES)
        by_name = {i.filename: i for i in infos}
        for i in infos:
            if i.flag_bits & 0x1 or i.compress_type not in (zipfile.ZIP_STORED,
                                                             zipfile.ZIP_DEFLATED):
                raise RestoreError(E_ZIP)
        if by_name[MANIFEST].file_size > MAX_MANIFEST_BYTES:
            raise RestoreError(E_MANIFEST)
        if by_name[DB_ENTRY].file_size > MAX_DB_BYTES:
            raise RestoreError(E_TOO_BIG)
        try:
            with z.open(by_name[MANIFEST]) as f:
                raw = f.read(MAX_MANIFEST_BYTES + 1)
            if len(raw) > MAX_MANIFEST_BYTES:
                raise RestoreError(E_MANIFEST)
            manifest = _parse_manifest(raw)
            digest, size = _extract_db(z, by_name[DB_ENTRY], part)
        except (zipfile.BadZipFile, zlib.error, EOFError, OSError, NotImplementedError) as exc:
            raise RestoreError(E_ZIP) from exc
    if size != manifest["db_size"] or not secrets.compare_digest(digest, manifest["db_sha256"]):
        raise RestoreError(E_CHECKSUM)
    return manifest


def _publish(part: Path, final: Path) -> None:
    os.replace(part, final)


def _swap(staged: Path, db_path: Path) -> None:
    """Austausch in einem Schritt (gleicher Ordner, gleiches Laufwerk)."""
    os.replace(staged, db_path)


def stage_upload(path: Path, staging: Path) -> Staged:
    """Prüft eine hochgeladene .kassivo-Datei und stellt die Datenbank bereit."""
    token, part, final = _new_slot(Path(staging))
    try:
        manifest = _read_zip(Path(path), part)
        info = check_db(part)
        if info["schema"] != manifest["schema_version"]:
            raise RestoreError(E_MANIFEST)
        _publish(part, final)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return Staged(token, final, info["schema"], info,
                  manifest["_created"].strftime("%d.%m.%Y %H:%M"), manifest["app_version"])


def stage_local(src: Path, staging: Path) -> Staged:
    """Stellt eine Sicherung aus backups/ bereit (als Kopie, das Original bleibt)."""
    m = backup.NAME_RE.fullmatch(Path(src).name)
    if not m:
        raise RestoreError(E_NOT_DB)
    token, part, final = _new_slot(Path(staging))
    try:
        if Path(src).stat().st_size > MAX_DB_BYTES:
            raise RestoreError(E_TOO_BIG)
        shutil.copyfile(src, part)
        info = check_db(part)
        _publish(part, final)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    d, t = m[1], m[2]
    created = f"{d[6:]}.{d[4:6]}.{d[:4]} {t[:2]}:{t[2:4]}:{t[4:]}"
    return Staged(token, final, info["schema"], info, created, None, Path(src).name)


def format_day(value) -> str | None:
    """JJJJ-MM-TT als TT.MM.JJJJ; Unbekanntes unverändert (wird beim Anzeigen escaped)."""
    if value is None:
        return None
    m = _DATE_RE.fullmatch(str(value))
    return f"{m[3]}.{m[2]}.{m[1]}" if m else str(value)[:20]


# --- Einspielen ---

def _safety_copy(db_path: Path, backup_dir: Path, timeout: float) -> str | None:
    if not db_path.exists():
        return None
    try:
        c = sqlite3.connect(str(db_path), timeout=timeout)
    except sqlite3.Error as exc:
        raise RestoreError(E_SAFETY) from exc
    try:
        return backup.backup_db(c, backup_dir).name
    except sqlite3.OperationalError as exc:
        text = str(exc).lower()
        raise RestoreError(E_BUSY if "locked" in text or "busy" in text else E_SAFETY) from exc
    except Exception as exc:
        raise RestoreError(E_SAFETY) from exc
    finally:
        c.close()


def restore(db_path: Path, token: str, staging: Path, backup_dir: Path,
            timeout: float = 5.0) -> Restored:
    """Ersetzt db_path durch die bereitgestellte Sicherung.

    Der Aufrufer darf keine Verbindung zu db_path offen halten (Windows sperrt die Datei)."""
    db_path, staging = Path(db_path), Path(staging)
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        raise RestoreError(E_EXPIRED)
    staged = staging / f"{token}.db"
    try:
        if not stat.S_ISREG(staged.lstat().st_mode):
            raise RestoreError(E_EXPIRED)
    except FileNotFoundError as exc:
        raise RestoreError(E_EXPIRED) from exc
    check_db(staged)  # erneut: die Datei lag zwischen Vorschau und Bestätigung auf der Platte
    # Ältere Schema-Version: bestehende Migration auf der Kopie, mit Sicherung vorher.
    db.connect(staged, backup_dir=backup_dir).close()
    info = check_db(staged)
    backup._fsync_file(staged)
    safety = _safety_copy(db_path, backup_dir, timeout)
    # Ein Journal neben der Datenbank gehört zu einer offenen Transaktion eines anderen
    # Programms. Es darf nie auf die eingespielte Datei angewendet werden.
    for suffix in ("-journal", "-wal"):
        if Path(str(db_path) + suffix).exists():
            raise RestoreError(E_BUSY)
    try:
        _swap(staged, db_path)
    except OSError as exc:
        raise RestoreError(E_REPLACE.format(name=safety or "keine")) from exc
    backup._fsync_dir(db_path.parent)
    return Restored(safety, info["members"], info["transactions"])
