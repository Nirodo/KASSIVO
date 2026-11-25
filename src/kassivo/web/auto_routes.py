"""Automatische Verteilung (#27): Liste, Rückgängig, offene Vorschläge neu verteilen."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from kassivo import dues
from kassivo.web.common import check_id, get_conn, render, with_note

router = APIRouter()
PROPOSED_URL = "/transactions?year=all&proposed=1"


@router.get("/auto")
async def auto_list(request: Request, conn=Depends(get_conn)):
    return render(request, "auto.html", rows=dues.auto_rows(conn))


@router.post("/auto/{tx_id:int}/undo")
async def auto_undo(request: Request, tx_id: int, conn=Depends(get_conn)):
    check_id(tx_id)
    if conn.execute('select 1 from "transaction" where id=?', (tx_id,)).fetchone() is None:
        raise HTTPException(404)
    try:
        dues.undo_auto(conn, tx_id)
    except ValueError as e:  # feste Meldungen aus dues, keine Nutzereingaben
        return render(request, "error.html", 422, message=str(e), back="/auto",
                      back_label="Zu „Automatisch verteilt“")
    return RedirectResponse(with_note("/auto", "undone", 1), status_code=303)


@router.post("/auto/run")
async def auto_run(conn=Depends(get_conn)):
    res = dues.auto_distribute_pending(conn)
    return RedirectResponse(
        with_note(PROPOSED_URL, "run", res.total, a=res.confirmed, r=res.review, o=res.outside),
        status_code=303)
