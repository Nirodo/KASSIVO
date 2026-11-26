"""Automatische Verteilung (#27): Liste, Rückgängig, offene Vorschläge neu verteilen."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from kassivo import dues
from kassivo.web.common import check_id, get_conn, render, with_note

router = APIRouter()
PROPOSED_URL = "/transactions?year=all&proposed=1"
PAGE_SIZE = 100
MAX_PAGE = 100_000
_PAGE_RE = re.compile(r"[0-9]{1,6}", re.ASCII)


@router.get("/auto")
async def auto_list(request: Request, conn=Depends(get_conn)):
    page = request.query_params.get("page", "").strip() or "1"
    if not _PAGE_RE.fullmatch(page) or not 1 <= int(page) <= MAX_PAGE:
        return render(request, "error.html", 422,
                      message=f"Seite muss eine Zahl von 1 bis {MAX_PAGE} sein.",
                      back="/auto", back_label="Zu „Automatisch verteilt“")
    page = int(page)
    pages = max(1, -(-dues.auto_count(conn) // PAGE_SIZE))
    return render(request, "auto.html", page=page, pages=pages,
                  rows=dues.auto_rows(conn, PAGE_SIZE, (page - 1) * PAGE_SIZE),
                  prev_url=f"/auto?page={page - 1}" if page > 1 else None,
                  next_url=f"/auto?page={page + 1}" if page < pages else None)


@router.post("/auto/{tx_id:int}/undo")
async def auto_undo(request: Request, tx_id: int, conn=Depends(get_conn)):
    check_id(tx_id)
    if conn.execute('select 1 from "transaction" where id=?', (tx_id,)).fetchone() is None:
        raise HTTPException(404)
    member_id = conn.execute('select member_id from "transaction" where id=?',
                             (tx_id,)).fetchone()[0]
    before = dues.auto_snapshot(conn, member_id) if member_id else {}
    try:
        dues.undo_auto(conn, tx_id)
    except ValueError as e:  # feste Meldungen aus dues, keine Nutzereingaben
        return render(request, "error.html", 422, message=str(e), back="/auto",
                      back_label="Zu „Automatisch verteilt“")
    moved = dues.count_changed(before, dues.auto_snapshot(conn, member_id), exclude=tx_id)
    url = with_note("/auto", "undone_r", 1, moved) if moved else with_note("/auto", "undone", 1)
    return RedirectResponse(url, status_code=303)


@router.post("/auto/run")
async def auto_run(conn=Depends(get_conn)):
    res = dues.auto_distribute_pending(conn)
    return RedirectResponse(
        with_note(PROPOSED_URL, "run_outside", res.total, a=res.confirmed, r=res.review,
                  o=res.outside) if res.outside else
        with_note(PROPOSED_URL, "run", res.total, a=res.confirmed, r=res.review),
        status_code=303)
