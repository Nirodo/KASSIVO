"""Sicherung der Datenbank: Seite und Auslöser."""
from fastapi import APIRouter, Depends, Request

from kassivo.backup import backup_db, list_backups
from kassivo.web.common import get_conn, render

router = APIRouter()
E_MEMORY = "Sicherung nur bei dateibasierter Datenbank möglich."


def _backup_dir(request: Request):
    path = request.app.state.db_path
    return None if str(path) == ":memory:" else path.parent / "backups"


def _page(request, status=200, created=None, error=None):
    return render(request, "backup.html", status, created=created, error=error,
                  backups=list_backups(_backup_dir(request)))


@router.get("/backup")
async def backup_page(request: Request):
    return _page(request)


@router.post("/backup")
async def backup_create(request: Request, conn=Depends(get_conn)):
    dest = _backup_dir(request)
    if dest is None:
        return _page(request, 422, error=E_MEMORY)
    return _page(request, created=backup_db(conn, dest).name)
