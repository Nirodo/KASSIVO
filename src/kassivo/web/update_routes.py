"""Einstellungen > Update (#31): Opt-in, nach Updates suchen, Paket herunterladen.

Ausgehende Verbindungen gibt es nur auf Knopfdruck (POST) und nur, wenn die Update-Prüfung
eingeschaltet ist. GET-Anfragen bauen nie eine Verbindung auf. Installiert wird mit update.bat.
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from kassivo import __version__, update
from kassivo.db import get_setting, set_setting
from kassivo.settings import update_dir
from kassivo.web.common import get_conn, render

router = APIRouter()
E_OFF = "Die Update-Prüfung ist ausgeschaltet. Bitte zuerst einschalten und speichern."
E_MEMORY = "Herunterladen nur bei dateibasierter Datenbank möglich."
CURRENT = "Sie haben die aktuelle Version."


def _enabled(conn) -> bool:
    return get_setting(conn, update.SETTING_KEY, "0") == "1"


def _fetch(request: Request):
    """Injizierbar für Tests (app.state.update_fetch), sonst der echte Client."""
    return getattr(request.app.state, "update_fetch", None) or update.urllib_fetch


def _update_dir(request: Request):
    path = request.app.state.db_path
    return None if str(path) == ":memory:" else update_dir(path)


def _downloaded(request: Request) -> str | None:
    d = _update_dir(request)
    package = update.find_package(d, __version__) if d is not None else None
    return package.name[len("kassivo-"):-len(".zip")] if package else None


def _page(request, conn, status=200, **ctx):
    return render(request, "update.html", status, enabled=_enabled(conn), current=__version__,
                  downloaded=_downloaded(request), ok=request.query_params.get("ok") == "1",
                  current_text=CURRENT, **ctx)


@router.get("/settings/update")
async def update_page(request: Request, conn=Depends(get_conn)):
    return _page(request, conn)


@router.post("/settings/update")
async def update_toggle(request: Request, enabled: str = Form(""), conn=Depends(get_conn)):
    set_setting(conn, update.SETTING_KEY, "1" if enabled == "1" else "0")
    return RedirectResponse("/settings/update?ok=1", status_code=303)


@router.post("/settings/update/check")
async def update_check(request: Request, conn=Depends(get_conn)):
    if not _enabled(conn):
        return _page(request, conn, 409, error=E_OFF)
    try:
        release = await run_in_threadpool(update.check_latest, _fetch(request))
    except update.UpdateError as exc:
        return _page(request, conn, error=str(exc))
    return _page(request, conn, release=release,
                 newer=update.is_newer(release.version, __version__))


@router.post("/settings/update/download")
async def update_download(request: Request, conn=Depends(get_conn)):
    if not _enabled(conn):
        return _page(request, conn, 409, error=E_OFF)
    dest = _update_dir(request)
    if dest is None:
        return _page(request, conn, 422, error=E_MEMORY)
    fetch = _fetch(request)
    try:
        # URLs kommen nie aus dem Formular: das Release wird frisch abgefragt
        release = await run_in_threadpool(update.check_latest, fetch)
        if not update.is_newer(release.version, __version__):
            return _page(request, conn, release=release, newer=False)
        await run_in_threadpool(update.download_release, release, dest, fetch)
    except update.UpdateError as exc:
        return _page(request, conn, error=str(exc))
    return _page(request, conn, release=release, newer=True, just_downloaded=True)
