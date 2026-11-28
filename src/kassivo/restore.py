"""Sicherung für einen anderen PC: Export als .kassivo-Datei, Prüfen und Einspielen (#32).

Eine .kassivo-Datei ist ein ZIP mit genau zwei Einträgen: manifest.json (Programmversion,
Schema-Version, Erstellzeit, SHA-256 und Größe der Datenbank) und kassivo.db (konsistente
Kopie über die SQLite-Backup-API). Nicht verschlüsselt: sie enthält Klarnamen und IBANs.

Einspielen in zwei Schritten:
  1. stage_upload / stage_local: Datei als Kopie in den Bereitstellungsordner neben der
     Datenbank legen, prüfen (Kopf, integrity_check, keine Trigger/Views/virtuellen Tabellen),
     ältere Schema-Version mit der bestehenden Migration umstellen (ohne Sicherung: das
     Original bleibt ja erhalten), dann streng gegen eine Referenz-Datenbank derselben
     Schema-Version vergleichen und Geldspalten auf ganze Zahlen prüfen. Die aktuelle
     Datenbank bleibt unberührt.
  2. restore: bereitgestellte Datei erneut streng prüfen, Journal neben der aktuellen
     Datenbank ausschließen, Sicherheitskopie anlegen, dann atomar per os.replace austauschen.

Fremde Dateien werden nur gehärtet geöffnet (trusted_schema=OFF, SQLITE_DBCONFIG_DEFENSIVE).

Verworfen:
  - ZIP entpacken (extractall): Namen aus dem Archiv werden nie als Pfad benutzt, es gibt nur
    die zwei festen Einträge; so ist Zip-Slip ausgeschlossen.
  - Erst ersetzen, dann migrieren: Scheitert die Migration, wäre die aktive Datenbank halb
    umgestellt. Migriert wird deshalb die bereitgestellte Kopie.
  - Struktur nur über Tabellennamen prüfen: lässt fehlende UNIQUE-Bedingungen, REAL-Beträge
    oder virtuelle Tabellen durch. Verglichen wird deshalb mit einer frisch angelegten und
    migrierten Referenz (normalisierter CREATE-Text, table_xinfo, Fremdschlüssel, Indizes).
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
import time
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
SIDECARS = ("-journal", "-wal", "-shm")
TOKEN_RE = re.compile(r"[0-9a-f]{32}", re.ASCII)
_STAGED_RE = re.compile(
    r"(?:[0-9a-f]{32}\.(?:db|done)|\.[0-9a-f]{32}\.part)(?:-journal|-wal|-shm)?", re.ASCII)
# Eigene Zwischenordner im Datenordner (tempfile.mkdtemp: Präfix + Zufallszeichen)
_LEFTOVER_RE = re.compile(r"\.(?:export|upload)-[A-Za-z0-9_]{1,64}", re.ASCII)
LEFTOVER_MAX_AGE = 3600  # Sekunden; jüngere Ordner können zu einer laufenden Anfrage gehören
_VERSION_RE = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", re.ASCII)
_SHA_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_DATE_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", re.ASCII)
# Tabellen der Ausgangsfassung (Schema 1); jede KASSIVO-Datenbank hat sie.
REQUIRED_TABLES = frozenset({
    "account", "membership_type", "fee_rate", "member", "member_type_history", "member_iban",
    "category", "category_rule", "import_batch", "transaction", "allocation", "settings"})
# Geldspalten: (Tabelle, Spalte, NULL erlaubt). Beträge sind immer ganze Cent.
MONEY_COLUMNS = (("transaction", "amount_cents", False), ("allocation", "cents", False),
                 ("fee_rate", "cents", False), ("category_rule", "min_cents", True),
                 ("category_rule", "max_cents", True))

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
E_MONEY = ("Die Datenbank in der Sicherung enthält Beträge, die keine ganzen Cent sind. "
           "Sie stammt nicht unverändert aus KASSIVO.")
E_MIGRATE = ("Die Sicherung konnte nicht auf das Datenbankformat dieser KASSIVO-Version "
             "umgestellt werden.")
E_EXPIRED = "Die Vorschau ist nicht mehr gültig. Bitte die Sicherung erneut auswählen."
E_DONE = ("Diese Sicherung wurde bereits eingespielt (zum Beispiel nach einem Doppelklick). "
          "Es wurde nichts weiter geändert.")
E_BUSY = ("Die aktuelle Datenbank ist gerade von einem anderen Programm oder einer zweiten "
          "KASSIVO-Instanz geöffnet. Es wurde nichts ersetzt.")
E_SAFETY = ("Die Sicherheitskopie der aktuellen Datenbank ist fehlgeschlagen. "
            "Es wurde nichts ersetzt.")
E_FRESH = "Die neue, leere Datenbank konnte nicht angelegt werden. Es wurde nichts gelöscht."
E_REPLACE = ("Die Datenbank konnte nicht ersetzt werden, vermutlich ist sie von einem anderen "
             "Programm geöffnet. Die bisherige Datenbank ist unverändert. "
             "Sicherheitskopie: {name}.")


class RestoreError(Exception):
    """Sicherung kann nicht eingespielt werden; die Meldung ist für Nutzer gedacht."""


@dataclass
class Staged:
    token: str
    path: Path
    schema: int           # Schema-Version der Sicherung vor der Umstellung
    summary: dict
    created: str          # Anzeige, TT.MM.JJJJ HH:MM
    app_version: str | None
    source: str | None = None  # Dateiname bei einer Sicherung aus backups/


@dataclass
class Restored:
    safety: str | None    # Dateiname der Sicherheitskopie in backups/
    members: int
    transactions: int


# --- Verbindungen ---

def harden(conn: sqlite3.Connection) -> None:
    """Für Dateien, die nicht von dieser KASSIVO-Instanz stammen."""
    if hasattr(conn, "setconfig"):  # Python 3.12+
        conn.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
        conn.setconfig(sqlite3.SQLITE_DBCONFIG_TRUSTED_SCHEMA, False)
    conn.execute("pragma trusted_schema = OFF")


def _open(path: Path) -> sqlite3.Connection:
    """Nur für eigene Kopien (Bereitstellung, Export-Schnappschuss), nie die Datei des Nutzers."""
    conn = sqlite3.connect(str(path), timeout=1.0)
    try:
        harden(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _remove(path: Path) -> None:
    """Datei samt -journal/-wal/-shm entfernen; Fehler verdecken nie den eigentlichen Fehler."""
    for p in [path] + [Path(str(path) + s) for s in SIDECARS]:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


# --- Export ---

def export_name(now: datetime | None = None) -> str:
    return "kassivo-backup-" + (now or datetime.now()).strftime("%Y%m%d-%H%M%S") + ".kassivo"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


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
        _remove(snap)
    return manifest


# --- Manifest ---

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


# --- Struktur ---

def _norm(sql) -> str:
    """CREATE-Text ohne Leerraum, Anführungszeichen, Groß/Klein und IF NOT EXISTS."""
    return re.sub(r'[\s"`\[\]]', "", (sql or "").lower()).replace("ifnotexists", "")


def _objects(conn) -> list:
    return sorted((t, n, tb, _norm(s)) for t, n, tb, s in conn.execute(
        "select type, name, tbl_name, sql from sqlite_master "
        "where name not like 'sqlite\\_%' escape '\\'").fetchall())


def _rows(conn, sql) -> list:
    """Als Tupel (die Referenz-Verbindung liefert sqlite3.Row)."""
    return [tuple(r) for r in conn.execute(sql).fetchall()]


def _details(conn, tables) -> list:
    """Spalten, Fremdschlüssel und Indizes (auch die automatischen für UNIQUE/PRIMARY KEY)."""
    out = []
    for name in sorted(tables):
        q = name.replace('"', '""')
        out.append(("xinfo", name, _rows(conn, f'pragma table_xinfo("{q}")')))
        out.append(("fk", name, _rows(conn, f'pragma foreign_key_list("{q}")')))
        indexes = []
        for row in _rows(conn, f'pragma index_list("{q}")'):
            # (seq, name, unique, origin, partial); Name der Auto-Indizes hängt von der
            # Reihenfolge ab, ist aber für gleichen CREATE-Text gleich.
            iq = row[1].replace('"', '""')
            cols = _rows(conn, f'pragma index_xinfo("{iq}")')
            indexes.append((row[1], row[2], row[3], row[4], cols))
        out.append(("idx", name, sorted(indexes)))
    return out


def _signature(conn):
    objects = _objects(conn)
    tables = [n for t, n, _, _ in objects if t == "table"]
    return objects, tables


def _reference():
    """Frisch angelegte und migrierte Datenbank im Speicher: Sollzustand dieser Version."""
    ref = db.connect(":memory:", on_open=harden)
    try:
        objects, tables = _signature(ref)
        return objects, _details(ref, tables)
    finally:
        ref.close()


def _quick_check(c) -> int:
    """Vor jeder Umstellung: Version, Unversehrtheit, keine Trigger/Views/virtuellen Tabellen."""
    version = db.schema_version(c)
    target = db.current_schema_version(db.MIGRATIONS)
    if version > target:
        raise db._too_new(version, target)
    if c.execute("pragma integrity_check").fetchall() != [("ok",)]:
        raise RestoreError(E_CORRUPT)
    rows = c.execute("select type, name, sql from sqlite_master").fetchall()
    tables = {n for t, n, _ in rows if t == "table"}
    # Trigger würden bei der Umstellung oder später beim Schreiben laufen.
    if (not REQUIRED_TABLES <= tables
            or any(t in ("trigger", "view") for t, _, _ in rows)
            or any(_norm(s).startswith("createvirtualtable") for _, _, s in rows)):
        raise RestoreError(E_SCHEMA)
    return version


def _strict_check(c) -> dict:
    """Nach der Umstellung: genau der Aufbau der Referenz, Beträge nur ganze Cent."""
    if db.schema_version(c) != db.current_schema_version(db.MIGRATIONS):
        raise RestoreError(E_SCHEMA)
    if c.execute("pragma integrity_check").fetchall() != [("ok",)]:
        raise RestoreError(E_CORRUPT)
    ref_objects, ref_details = _reference()
    objects, tables = _signature(c)
    if objects != ref_objects or _details(c, tables) != ref_details:
        raise RestoreError(E_SCHEMA)
    for table, col, nullable in MONEY_COLUMNS:
        cond = f"typeof({col}) != 'integer'" + (f" and {col} is not null" if nullable else "")
        if c.execute(f'select count(*) from "{table}" where {cond}').fetchall()[0][0]:
            raise RestoreError(E_MONEY)
    return summary(c)


def _header_ok(path: Path) -> None:
    with open(path, "rb") as f:
        if f.read(len(SQLITE_MAGIC)) != SQLITE_MAGIC:
            raise RestoreError(E_NOT_DB)


def _with_conn(path: Path, check):
    try:
        c = _open(path)
    except sqlite3.DatabaseError as exc:
        raise RestoreError(E_NOT_DB) from exc
    try:
        return check(c)
    except sqlite3.DatabaseError as exc:
        raise RestoreError(E_CORRUPT) from exc
    finally:
        c.close()


def _set_delete_mode(c) -> None:
    """Rollback-Journal statt WAL; sonst entstehen -wal/-shm neben der aktiven Datenbank."""
    if c.execute("pragma journal_mode = delete").fetchall()[0][0].lower() != "delete":
        raise RestoreError(E_CORRUPT)


def verify(path: Path) -> dict:
    """Strenge Prüfung einer bereitgestellten Kopie (aktuelles Schema). Gibt summary() zurück."""
    path = Path(path)
    _header_ok(path)

    def check(c):
        _set_delete_mode(c)
        return _strict_check(c)
    info = _with_conn(path, check)
    if any(os.path.exists(str(path) + s) for s in SIDECARS):
        raise RestoreError(E_CORRUPT)
    return info


def prepare(path: Path) -> tuple[int, dict]:
    """Prüft, stellt ältere Versionen um (ohne Sicherung) und prüft streng.

    Gibt (Schema-Version vorher, summary) zurück. Fehler: RestoreError, SchemaTooNewError."""
    path = Path(path)
    _header_ok(path)
    original = _with_conn(path, _quick_check)
    try:
        db.connect(path, on_open=harden, backup_before_migration=False, timeout=1.0).close()
    except db.SchemaTooNewError:
        raise
    except (db.DatabaseOpenError, sqlite3.Error) as exc:
        raise RestoreError(E_MIGRATE) from exc
    return original, verify(path)


# --- Bereitstellen ---

def clear_staging(staging: Path) -> None:
    """Entfernt frühere Bereitstellungen und Erledigt-Marker (nur eigene Namen, keine Links)."""
    if not Path(staging).is_dir():
        return
    for p in Path(staging).iterdir():
        if not _STAGED_RE.fullmatch(p.name):
            continue
        try:
            if stat.S_ISREG(p.lstat().st_mode):
                p.unlink()
        except OSError:
            pass


def cleanup_leftovers(data_dir: Path, max_age: float = LEFTOVER_MAX_AGE) -> None:
    """Löscht eigene Zwischenordner .export-*/.upload-*, die älter als max_age Sekunden sind.

    Warum ein Alter: Anfragen laufen im selben Prozess nebeneinander (ein Export wird noch
    gestreamt, während ein Upload beginnt). Ein jüngerer Ordner kann noch in Gebrauch sein.
    Beim Start (keine Anfrage läuft) wird mit max_age=0 alles Eigene entfernt."""
    if not Path(data_dir).is_dir():
        return
    now = time.time()
    for p in Path(data_dir).iterdir():
        if not _LEFTOVER_RE.fullmatch(p.name):
            continue
        try:
            st = p.lstat()
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode) and now - st.st_mtime >= max_age:
            shutil.rmtree(p, ignore_errors=True)


def startup_cleanup(db_path: Path) -> None:
    """Beim Start: alle eigenen Reste neben der Datenbank entfernen."""
    data = Path(db_path).parent
    cleanup_leftovers(data, max_age=0)
    clear_staging(data / "restore")


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


def stage_upload(path: Path, staging: Path) -> Staged:
    """Prüft eine hochgeladene .kassivo-Datei und stellt die Datenbank bereit."""
    token, part, final = _new_slot(Path(staging))
    try:
        manifest = _read_zip(Path(path), part)
        _header_ok(part)
        with open(part, "rb") as f:
            f.seek(60)
            header_version = int.from_bytes(f.read(4), "big")  # user_version im Dateikopf
        if header_version != manifest["schema_version"]:
            raise RestoreError(E_MANIFEST)
        original, info = prepare(part)
        _publish(part, final)
    except BaseException:
        _remove(part)
        raise
    return Staged(token, final, original, info,
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
        original, info = prepare(part)
        _publish(part, final)
    except BaseException:
        _remove(part)
        raise
    d, t = m[1], m[2]
    created = f"{d[6:]}.{d[4:6]}.{d[:4]} {t[:2]}:{t[2:4]}:{t[4:]}"
    return Staged(token, final, original, info, created, None, Path(src).name)


def format_day(value) -> str | None:
    """JJJJ-MM-TT als TT.MM.JJJJ; Unbekanntes unverändert (wird beim Anzeigen escaped)."""
    if value is None:
        return None
    m = _DATE_RE.fullmatch(str(value))
    return f"{m[3]}.{m[2]}.{m[1]}" if m else str(value)[:20]


# --- Einspielen ---

def _sidecar_present(db_path: Path) -> bool:
    return any(os.path.exists(str(db_path) + s) for s in SIDECARS)


def _ensure_no_journal(db_path: Path, timeout: float) -> None:
    """Ein Journal neben der Datenbank darf nie auf die eingespielte Datei angewendet werden.

    Liegt eines da, wird einmal lesend geöffnet: ein heißes Journal nach einem Absturz rollt
    SQLite dabei zurück und löscht es. Bleibt es (offene Transaktion eines anderen Programms),
    wird abgebrochen, bevor irgendetwas angelegt wird."""
    if not _sidecar_present(db_path):
        return
    try:
        c = sqlite3.connect(str(db_path), timeout=timeout)
        try:
            c.execute("select count(*) from sqlite_master").fetchall()
        finally:
            c.close()
    except sqlite3.Error as exc:
        raise RestoreError(E_BUSY) from exc
    if _sidecar_present(db_path):
        raise RestoreError(E_BUSY)


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


def _swap(staged: Path, db_path: Path) -> None:
    """Austausch in einem Schritt (gleicher Ordner, gleiches Laufwerk)."""
    os.replace(staged, db_path)


def restore(db_path: Path, token: str, staging: Path, backup_dir: Path,
            timeout: float = 5.0) -> Restored:
    """Ersetzt db_path durch die bereitgestellte Sicherung.

    Der Aufrufer darf keine Verbindung zu db_path offen halten (Windows sperrt die Datei)."""
    db_path, staging = Path(db_path), Path(staging)
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        raise RestoreError(E_EXPIRED)
    staged, done = staging / f"{token}.db", staging / f"{token}.done"
    try:
        if not stat.S_ISREG(staged.lstat().st_mode):
            raise RestoreError(E_EXPIRED)
    except FileNotFoundError as exc:
        raise RestoreError(E_DONE if done.exists() else E_EXPIRED) from exc
    try:
        # erneut: die Datei lag zwischen Vorschau und Bestätigung auf der Platte
        info = verify(staged)
    except (RestoreError, db.DatabaseOpenError):
        _remove(staged)
        raise
    safety = _replace(db_path, staged, backup_dir, timeout)
    try:
        done.touch()  # Doppelklick: zweite Anfrage bekommt eine klare Meldung
    except OSError:
        pass
    return Restored(safety, info["members"], info["transactions"])


def _replace(db_path: Path, staged: Path, backup_dir: Path, timeout: float) -> str | None:
    """Journal ausschließen, Sicherheitskopie anlegen, atomar austauschen.

    staged ist bereits streng geprüft. Gibt den Namen der Sicherheitskopie zurück."""
    _ensure_no_journal(db_path, timeout)
    safety = _safety_copy(db_path, backup_dir, timeout)
    if _sidecar_present(db_path):  # zwischen Kopie und Austausch geöffnet
        raise RestoreError(E_BUSY)
    backup._fsync_file(staged)
    try:
        _swap(staged, db_path)
    except OSError as exc:
        raise RestoreError(E_REPLACE.format(name=safety or "keine")) from exc
    backup._fsync_dir(db_path.parent)
    return safety


# --- Neu anfangen (#65) ---

def reset(db_path: Path, staging: Path, backup_dir: Path, timeout: float = 5.0) -> str | None:
    """Ersetzt db_path durch eine frische Datenbank (wie beim ersten Start).

    Gleiche Schutzmechanismen wie restore(): die neue Datei entsteht im
    Bereitstellungsordner (gleiches Laufwerk), wird streng geprüft, und erst nach der
    Sicherheitskopie atomar ausgetauscht. Scheitert ein Schritt, bleibt die alte Datenbank.
    Danach werden eigene Reste in staging entfernt. Gibt den Namen der Sicherheitskopie zurück.

    Verworfen: Tabellen leeren (delete from ...). Das hinterließe alte Daten in freien
    Seiten der Datei, Einstellungen und Schema-Reste müssten einzeln behandelt werden, und
    ein Abbruch mitten drin ergäbe einen Teilzustand."""
    db_path, staging = Path(db_path), Path(staging)
    _, part, _ = _new_slot(staging)
    try:
        try:
            db.connect(part, backup_before_migration=False, timeout=timeout).close()
        except (db.DatabaseOpenError, sqlite3.Error) as exc:
            raise RestoreError(E_FRESH) from exc
        verify(part)
        safety = _replace(db_path, part, Path(backup_dir), timeout)
    except BaseException:
        _remove(part)
        raise
    clear_staging(staging)
    return safety
