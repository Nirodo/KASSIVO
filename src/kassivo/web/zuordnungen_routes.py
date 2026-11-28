"""Gemerkte Zuordnungen (#55): ansehen, ändern, vergessen.

Ändern und Vergessen ordnen keine Zahlungen um. IBANs erscheinen nur
maskiert, nie in URLs oder Fehlertexten; die Einträge laufen über die rowid.
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from kassivo import matching
from kassivo.iban import normalize_iban
from kassivo.ingest import mask_iban
from kassivo.members import list_members
from kassivo.web.common import check_id, get_conn, parse_id, render, with_note

router = APIRouter()
MULTI = "multi"  # Auswahl "zahlt für mehrere Mitglieder"
STALE = ("Diese gemerkte Zuordnung gibt es nicht mehr. Vielleicht wurde sie schon"
         " geändert oder vergessen. Bitte die Seite neu laden.")


def _error(request, message: str, status: int):
    return render(request, "error.html", status, message=message, back="/zuordnungen",
                  back_label="Zu den gemerkten Zuordnungen")


def _exists(conn, entry_id: int) -> bool:
    return conn.execute("select 1 from member_iban where rowid=?",
                        (entry_id,)).fetchone() is not None


@router.get("/zuordnungen")
async def zuordnungen(request: Request, conn=Depends(get_conn)):
    rows = [{"id": r["id"], "member_id": r["member_id"], "member_name": r["member_name"],
             "iban": mask_iban(normalize_iban(r["iban"]) or r["iban"])}
            for r in matching.remembered(conn)]
    return render(request, "zuordnungen.html", rows=rows, members=list_members(conn),
                  multi=MULTI)


@router.post("/zuordnungen/{entry_id:int}")
async def zuordnung_save(request: Request, entry_id: int, member_id: str = Form(""),
                         conn=Depends(get_conn)):
    check_id(entry_id)
    if not _exists(conn, entry_id):
        return _error(request, STALE, 404)
    if member_id == MULTI:
        member = None
    else:
        member = parse_id(member_id)
        if member is None:
            return _error(request, "Bitte ein Mitglied oder „zahlt für mehrere“ wählen.", 422)
    try:
        matching.set_remembered(conn, entry_id, member)
    except ValueError:
        if not _exists(conn, entry_id):
            return _error(request, STALE, 404)
        return _error(request, "Unbekanntes Mitglied.", 422)
    return RedirectResponse(with_note("/zuordnungen", "remembered_saved", 0), status_code=303)


@router.post("/zuordnungen/{entry_id:int}/forget")
async def zuordnung_forget(request: Request, entry_id: int, conn=Depends(get_conn)):
    check_id(entry_id)
    try:
        matching.forget(conn, entry_id)
    except ValueError:
        return _error(request, STALE, 404)
    return RedirectResponse(with_note("/zuordnungen", "remembered_forgotten", 0),
                            status_code=303)
