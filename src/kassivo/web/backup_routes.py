"""Sicherung der Datenbank: Seite, Auslöser, Export und Einspielen (#32)."""
import re
import shutil
import stat
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse

from kassivo import __version__, restore
from kassivo.backup import NAME_RE, backup_db, delete_old, list_backups
from kassivo import db
from kassivo.restore import RestoreError
from kassivo.settings import backup_dir
from kassivo.web import import_routes
from kassivo.web.common import get_conn, render, with_note

router = APIRouter()
E_MEMORY = "Sicherung nur bei dateibasierter Datenbank möglich."
E_KEEP = "Bitte eine ganze Zahl von 1 bis 1000 angeben."
E_CONFIRM = "Bitte bestätigen, dass ältere Sicherungen gelöscht werden."
E_NO_FILE = "Bitte eine Datei auswählen."
E_TYPE = "Dateityp nicht erkannt. Erwartet wird eine KASSIVO-Sicherung (.kassivo)."
E_LOCAL = "Diese Sicherung gibt es nicht (mehr). Bitte eine Sicherung aus der Liste wählen."
E_RESTORE_CONFIRM = "Bitte bestätigen, dass die aktuelle Datenbank ersetzt wird."
NOTHING = " Es wurde nichts eingespielt."
MAX_UPLOAD_BYTES = import_routes.MAX_UPLOAD_BYTES
TOO_BIG = "Die Datei ist zu groß (Grenze 100 MB). Es wurde nichts eingespielt."
MAX_KEEP = 1000
_KEEP_RE = re.compile(r"[0-9]{1,4}")  # vor int(): keine riesigen Zahlen


def _db_path(request: Request):
    path = request.app.state.db_path
    return None if str(path) == ":memory:" else Path(path)


def _backup_dir(request: Request):
    path = _db_path(request)
    return None if path is None else backup_dir(path)


def _staging(request: Request):
    """Bereitstellungsordner neben der Datenbank: gleiches Laufwerk, also atomarer Austausch."""
    path = _db_path(request)
    return None if path is None else path.parent / "restore"


def _page(request, conn, status=200, created=None, error=None, safety=None):
    return render(request, "backup.html", status, created=created, error=error, safety=safety,
                  backups=list_backups(_backup_dir(request)),
                  schema=db.schema_version(conn),
                  program_schema=db.current_schema_version(db.MIGRATIONS))


def _page_own_conn(request, status, error):
    """Seite nach dem Einspielen: eigene, kurze Verbindung (vorher war keine offen)."""
    conn = db.connect(request.app.state.db_path)
    try:
        return _page(request, conn, status, error=error)
    finally:
        conn.close()


def _exists(dest, name):
    if dest is None:
        return False
    try:
        return stat.S_ISREG((dest / name).lstat().st_mode)
    except OSError:
        return False


def _valid_name(request, name):
    """Nur echte Sicherungen im Ordner melden, nie freien Text."""
    return name if NAME_RE.fullmatch(name) and _exists(_backup_dir(request), name) else None


@router.get("/backup")
async def backup_page(request: Request, conn=Depends(get_conn)):
    created = _valid_name(request, request.query_params.get("created", ""))
    safety = _valid_name(request, request.query_params.get("safety", ""))
    return _page(request, conn, created=created, safety=safety)


@router.post("/backup")
async def backup_create(request: Request, conn=Depends(get_conn)):
    dest = _backup_dir(request)
    if dest is None:
        return _page(request, conn, 422, error=E_MEMORY)
    name = backup_db(conn, dest).name
    return RedirectResponse(f"/backup?created={name}", status_code=303)


@router.post("/backup/cleanup")
async def backup_cleanup(request: Request, keep: str = Form(""), confirm: str = Form(""),
                         conn=Depends(get_conn)):
    dest = _backup_dir(request)
    if dest is None:
        return _page(request, conn, 422, error=E_MEMORY)
    if not _KEEP_RE.fullmatch(keep) or not 1 <= int(keep) <= MAX_KEEP:
        return _page(request, conn, 422, error=E_KEEP)
    if not confirm:
        return _page(request, conn, 422, error=E_CONFIRM)
    n, failed = delete_old(dest, int(keep))
    if failed:
        return RedirectResponse(with_note("/backup", "cleaned_partial", n, failed), status_code=303)
    return RedirectResponse(with_note("/backup", "cleaned", n), status_code=303)


# --- Für anderen PC exportieren ---

@router.post("/backup/export")
async def backup_export(request: Request, conn=Depends(get_conn)):
    path = _db_path(request)
    if path is None:
        return _page(request, conn, 422, error=E_MEMORY)
    # Zwischenordner im Datenordner, damit die Kopie neben der Datenbank liegt, die ohnehin
    # dieselben Daten enthält. Reste (Absturz, abgebrochener Download) räumen der Start und
    # cleanup_leftovers auf. Hinweis: hochgeladene Dateien legt Starlette unabhängig davon
    # zunächst im System-Temp ab.
    restore.cleanup_leftovers(path.parent)
    tmp = Path(tempfile.mkdtemp(prefix=".export-", dir=path.parent))
    name = restore.export_name()
    try:
        restore.export_backup(conn, tmp / name)
        size = (tmp / name).stat().st_size
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return StreamingResponse(
        _stream(tmp, tmp / name), media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"',
                 "Content-Length": str(size), "Cache-Control": "no-store"})


def _stream(tmp: Path, file: Path):
    """Liefert die Datei und löscht den Zwischenordner immer, auch bei Abbruch des Downloads
    (der Generator wird dann geschlossen, finally läuft)."""
    try:
        with open(file, "rb") as f:
            while chunk := f.read(import_routes.CHUNK):
                yield chunk
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- Sicherung einspielen ---

def _preview(request, conn, staged):
    current = restore.summary(conn)
    return render(request, "backup_restore.html", staged=staged, current=current,
                  version=__version__, program_schema=db.current_schema_version(db.MIGRATIONS),
                  day=restore.format_day)


def _stage_error(request, conn, exc):
    return _page(request, conn, 422, error=f"{exc}{NOTHING}")


@router.post("/backup/upload")
async def backup_upload(request: Request, file: UploadFile | None = File(None),
                        conn=Depends(get_conn)):
    staging = _staging(request)
    if staging is None:
        return _page(request, conn, 422, error=E_MEMORY)
    if file is None:
        return _page(request, conn, 422, error=E_NO_FILE)
    restore.cleanup_leftovers(_db_path(request).parent)
    with tempfile.TemporaryDirectory(prefix=".upload-", dir=_db_path(request).parent) as tmp:
        raw = Path(tmp) / "upload.bin"
        total, head = 0, b""
        with raw.open("wb") as out:
            while chunk := await file.read(import_routes.CHUNK):
                if total == 0:
                    head = chunk[:4]
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    return _page(request, conn, 413, error=TOO_BIG)
                out.write(chunk)
        if head != b"PK\x03\x04":
            return _page(request, conn, 422, error=E_TYPE + NOTHING)
        try:
            staged = restore.stage_upload(raw, staging)
        except (RestoreError, db.DatabaseOpenError) as exc:
            return _stage_error(request, conn, exc)
    return _preview(request, conn, staged)


@router.post("/backup/local")
async def backup_local(request: Request, name: str = Form(""), conn=Depends(get_conn)):
    dest = _backup_dir(request)
    if dest is None:
        return _page(request, conn, 422, error=E_MEMORY)
    if not _valid_name(request, name):
        return _page(request, conn, 422, error=E_LOCAL)
    try:
        staged = restore.stage_local(dest / name, _staging(request))
    except (RestoreError, db.DatabaseOpenError) as exc:
        return _stage_error(request, conn, exc)
    return _preview(request, conn, staged)


@router.post("/backup/cancel")
async def backup_cancel(request: Request):
    """Vorschau abbrechen: bereitgestellte Kopie sofort löschen."""
    staging = _staging(request)
    if staging is not None:
        restore.clear_staging(staging)
    return RedirectResponse("/backup", status_code=303)


@router.post("/backup/restore")
async def backup_restore(request: Request, token: str = Form(""), confirm: str = Form("")):
    # Absichtlich ohne get_conn: unter Windows verhindert eine offene Verbindung den Austausch.
    path, staging = _db_path(request), _staging(request)
    if path is None:
        return _page_own_conn(request, 422, E_MEMORY)
    if not confirm:
        return _page_own_conn(request, 422, E_RESTORE_CONFIRM)
    try:
        result = restore.restore(path, token, staging, backup_dir(path))
    except (RestoreError, db.DatabaseOpenError) as exc:
        return _page_own_conn(request, 422, str(exc))
    url = with_note("/backup", "restored", result.members, result.transactions)
    if result.safety:
        url += f"&safety={result.safety}"
    return RedirectResponse(url, status_code=303)
