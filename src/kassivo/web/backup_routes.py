"""Sicherung der Datenbank: Seite und Auslöser."""
from fastapi import APIRouter, Depends, Request

from kassivo.backup import backup_db, list_backups
from kassivo import db
from kassivo.settings import backup_dir
from kassivo.web.common import get_conn, render

router = APIRouter()
E_MEMORY = "Sicherung nur bei dateibasierter Datenbank möglich."


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
    return _page(request, conn)


@router.post("/backup")
async def backup_create(request: Request, conn=Depends(get_conn)):
    dest = _backup_dir(request)
    if dest is None:
        return _page(request, conn, 422, error=E_MEMORY)
    return _page(request, conn, created=backup_db(conn, dest).name)
