"""Einstellungen > Neu anfangen (#65): Datenbank löschen und frisch anlegen.

Bestätigung: das Wort LÖSCHEN eintippen (serverseitig geprüft). Vorher legt KASSIVO eine
Sicherheitskopie an; den Austausch übernimmt restore.reset mit denselben Schutzmechanismen
wie das Einspielen einer Sicherung.

Doppeltes Absenden: Das Formular trägt ein zufälliges Kennzeichen. Nach dem Erfolg liegt
dazu im Bereitstellungsordner ein Erledigt-Marker mit dem Namen der Sicherheitskopie. Kommt
dasselbe Kennzeichen erneut, wird nichts mehr gelöscht und derselbe Name gemeldet.
Verworfen: einfach erneut ausführen. Das zweite Absenden würde die schon leere Datenbank
sichern und deren Namen melden; der Name der Kopie mit den echten Daten ginge verloren.
"""
import secrets
import stat
import unicodedata

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from kassivo import db, restore
from kassivo.backup import NAME_RE, list_backups
from kassivo.restore import RestoreError
from kassivo.settings import backup_dir
from kassivo.web.backup_routes import E_MEMORY, _backup_dir, _db_path, _staging, _valid_name
from kassivo.web.common import get_conn, render

router = APIRouter()
WORD = "LÖSCHEN"
E_WORD = f"Bitte zur Bestätigung das Wort {WORD} eintippen. Es wurde nichts gelöscht."
E_FORM = "Das Formular ist nicht mehr gültig. Bitte die Seite neu laden. Es wurde nichts gelöscht."
MAX_MARKER = 64


def _page(request, conn, status=200, error=None):
    backups = list_backups(_backup_dir(request))
    done = request.query_params.get("done") == "1"
    return render(request, "settings_reset.html", status, error=error, done=done,
                  safety=_valid_name(request, request.query_params.get("safety", "")) if done
                  else None,
                  current=restore.summary(conn), last_backup=backups[0]["stamp"] if backups
                  else None, token=secrets.token_hex(16), word=WORD)


def _page_own_conn(request, status, error):
    """Nach dem Versuch: eigene, kurze Verbindung (während des Austauschs war keine offen)."""
    conn = db.connect(request.app.state.db_path)
    try:
        return _page(request, conn, status, error)
    finally:
        conn.close()


def _marker(request, token):
    return _staging(request) / f"{token}.done"


def _already_done(request, token) -> tuple[bool, str | None]:
    """(erledigt, Name der Sicherheitskopie). Nur eigene, gültige Namen werden übernommen."""
    p = _marker(request, token)
    try:
        if not stat.S_ISREG(p.lstat().st_mode):
            return False, None
        with open(p, "rb") as f:
            raw = f.read(MAX_MARKER + 1)
    except OSError:
        return False, None
    try:
        name = raw.decode("ascii")
    except UnicodeDecodeError:
        return True, None
    return True, (name if NAME_RE.fullmatch(name) else None)


def _done_url(safety):
    return "/settings/reset?done=1" + (f"&safety={safety}" if safety else "")


@router.get("/settings/reset")
async def reset_page(request: Request, conn=Depends(get_conn)):
    return _page(request, conn)


@router.post("/settings/reset")
async def reset_submit(request: Request, confirm: str = Form(""), token: str = Form("")):
    # Absichtlich ohne get_conn: unter Windows verhindert eine offene Verbindung den Austausch.
    path = _db_path(request)
    if path is None:
        return _page_own_conn(request, 422, E_MEMORY)
    if not restore.TOKEN_RE.fullmatch(token):
        return _page_own_conn(request, 422, E_FORM)
    if unicodedata.normalize("NFC", confirm.strip()) != WORD:
        return _page_own_conn(request, 422, E_WORD)
    done, previous = _already_done(request, token)
    if done:
        return RedirectResponse(_done_url(previous), status_code=303)
    try:
        safety = restore.reset(path, _staging(request), backup_dir(path))
    except (RestoreError, db.DatabaseOpenError) as exc:
        return _page_own_conn(request, 422, str(exc))
    try:
        _marker(request, token).write_bytes((safety or "").encode("ascii"))
    except OSError:
        pass  # nur für doppeltes Absenden; der Neuanfang selbst ist erledigt
    return RedirectResponse(_done_url(safety), status_code=303)
