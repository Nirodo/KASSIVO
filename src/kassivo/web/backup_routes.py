"""Sicherung der Datenbank: Seite und Auslöser."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from kassivo.backup import NAME_RE, backup_db, delete_old, list_backups
from kassivo import db
from kassivo.settings import backup_dir
from kassivo.web.common import get_conn, render, with_note

router = APIRouter()
E_MEMORY = "Sicherung nur bei dateibasierter Datenbank möglich."
E_KEEP = "Bitte eine ganze Zahl von 1 bis 1000 angeben."
E_CONFIRM = "Bitte bestätigen, dass ältere Sicherungen gelöscht werden."
MAX_KEEP = 1000


def _backup_dir(request: Request):
    path = request.app.state.db_path
    return None if str(path) == ":memory:" else backup_dir(path)


def _page(request, conn, status=200, created=None, error=None):
    return render(request, "backup.html", status, created=created, error=error,
                  backups=list_backups(_backup_dir(request)),
                  schema=db.schema_version(conn),
                  program_schema=db.current_schema_version(db.MIGRATIONS))


@router.get("/backup")
async def backup_page(request: Request, conn=Depends(get_conn)):
    created = request.query_params.get("created", "")
    return _page(request, conn, created=created if NAME_RE.fullmatch(created) else None)


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
    if not (keep.isascii() and keep.isdigit() and 1 <= int(keep) <= MAX_KEEP):
        return _page(request, conn, 422, error=E_KEEP)
    if not confirm:
        return _page(request, conn, 422, error=E_CONFIRM)
    n = delete_old(dest, int(keep))
    return RedirectResponse(with_note("/backup", "cleaned", n), status_code=303)
