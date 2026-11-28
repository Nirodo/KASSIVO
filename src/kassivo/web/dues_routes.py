"""Beitragsübersicht, Zahlungshistorie je Mitglied, Prognose.

Lesend bis auf die Beitragstabelle (#56): Bestätigen, Rückgängig und
Sammelbestätigung laufen über die Funktionen in dues.
"""
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from kassivo import dues
from kassivo.dues import (
    check_start_is_set, dues_grid, month_status, payment_history, proposed_months,
    proposed_tx_count,
)
from kassivo.forecast import forecast
from kassivo.members import type_at
from kassivo.months import add_months, month_end, month_of
from kassivo.overview import month_totals
from kassivo.web.common import (
    E_DATE, check_id, get_conn, get_today, parse_date, parse_id, parse_year, render, with_note,
    year_error, year_nav,
)

router = APIRouter()
E_HORIZON = "Das Datum liegt zu weit in der Zukunft (höchstens 50 Jahre)."

@router.get("/dues")
async def dues_view(request: Request, year: str | None = None,
                    conn=Depends(get_conn), today: date = Depends(get_today)):
    y = parse_year(year, today)
    if y is None:
        return year_error(request, "/dues")
    grid = dues_grid(conn, y, today)
    totals = [month_totals(grid, i) for i in range(12)]
    return render(request, "dues.html", year=y, grid=grid, totals=totals,
                  check_start_set=check_start_is_set(conn),
                  proposals=proposed_months(conn, y), waiting=proposed_tx_count(conn),
                  **year_nav(y))


E_STALE = ("Der Vorschlag hat sich geändert oder wurde schon entschieden. Es wurde nichts"
           " bestätigt. Bitte die aktuelle Verteilung prüfen.")
E_UNDO = "Nur automatisch verteilte Zahlungen dieses Mitglieds können rückgängig gemacht werden."
BACK_CELL = "Zurück zur Zahlung"
PREVIEW_LIMIT = 500


def _cell_member(conn, member_id: int, month: str):
    """Mitglied aus dem Pfad, Monat JJJJ-MM; sonst 404."""
    check_id(member_id)
    if not dues._valid_month(month):
        raise HTTPException(404)
    member = conn.execute("select id, name from member where id=?", (member_id,)).fetchone()
    if member is None:
        raise HTTPException(404)
    return member


@router.get("/dues/{member_id:int}/{month}")
async def dues_cell(request: Request, member_id: int, month: str,
                    conn=Depends(get_conn), today: date = Depends(get_today)):
    """Zahlungen eines Mitglieds für einen Monat (#56): Bestätigen, Ändern, Rückgängig."""
    member = _cell_member(conn, member_id, month)
    here = f"/dues/{member_id}/{month}"
    return render(request, "dues_cell.html", member=member, month=month,
                  ms=month_status(conn, member_id, month, today),
                  payments=dues.cell_payments(conn, member_id, month),
                  here_q=quote(here, safe=""), year=month[:4])


def _cell_error(request, here: str, status: int, message: str):
    return render(request, "error.html", status, message=message, back=here,
                  back_label=BACK_CELL)


@router.post("/dues/{member_id:int}/{month}/confirm")
async def dues_cell_confirm(request: Request, member_id: int, month: str,
                            pick: str = Form(""), conn=Depends(get_conn)):
    """Bestätigt genau den angezeigten Vorschlag (Signatur), sonst 409 ohne Änderung.

    Überzahlung ist hier erlaubt: die Seite hat sie angezeigt, der Nutzer entscheidet.
    """
    _cell_member(conn, member_id, month)
    here = f"/dues/{member_id}/{month}"
    parsed = dues.parse_pick(pick)
    if parsed is None or int(pick.split(":")[1]) != member_id:
        return _cell_error(request, here, 409, E_STALE)
    tx_id = parsed[0]
    before = dues.auto_snapshot(conn, member_id)
    if dues.confirm_proposal(conn, tx_id, pick) != "confirmed":
        return _cell_error(request, here, 409, E_STALE)
    moved = dues.count_changed(before, dues.auto_snapshot(conn, member_id), exclude=tx_id)
    return RedirectResponse(with_note(here, "dues_confirmed_r", 1, moved) if moved
                            else with_note(here, "dues_confirmed", 1), status_code=303)


@router.post("/dues/{member_id:int}/{month}/undo")
async def dues_cell_undo(request: Request, member_id: int, month: str,
                         tx_id: str = Form(""), conn=Depends(get_conn)):
    """Automatische Verteilung einer Zahlung dieses Mitglieds rückgängig machen."""
    _cell_member(conn, member_id, month)
    here = f"/dues/{member_id}/{month}"
    tid = parse_id(tx_id)
    row = None if tid is None else conn.execute(
        'select member_id from "transaction" where id=?', (tid,)).fetchone()
    if row is None or row["member_id"] != member_id:
        return _cell_error(request, here, 422, E_UNDO)
    before = dues.auto_snapshot(conn, member_id)
    try:
        dues.undo_auto(conn, tid)
    except ValueError:
        return _cell_error(request, here, 422, E_UNDO)
    moved = dues.count_changed(before, dues.auto_snapshot(conn, member_id), exclude=tid)
    return RedirectResponse(with_note(here, "undone_r", 1, moved) if moved
                            else with_note(here, "undone", 1), status_code=303)


@router.get("/dues/confirm-all")
async def confirm_all_preview(request: Request, conn=Depends(get_conn)):
    """Vorschau der Sammelbestätigung. Speichert nichts."""
    items = dues.proposal_preview(conn, PREVIEW_LIMIT)
    return render(request, "dues_confirm_all.html", items=items,
                  limited=len(items) >= PREVIEW_LIMIT, limit=PREVIEW_LIMIT)


@router.post("/dues/confirm-all")
async def confirm_all_submit(request: Request, conn=Depends(get_conn)):
    """Bestätigt nur ausgewählte, unveränderte Vorschläge der Vorschau.

    Ergebnis als Seite statt Weiterleitung, damit übersprungene Zahlungen mit
    Grund sichtbar sind. Erneutes Absenden bestätigt nichts doppelt: die
    Signaturen passen dann nicht mehr.
    """
    form = await request.form()
    picks = [v for v in form.getlist("pick") if isinstance(v, str)]
    res = dues.confirm_proposals(conn, picks) if picks else None
    skipped = []
    for why, ids in (("stale", res.stale), ("overpay", res.overpay)) if res else ():
        for tx_id in ids:
            skipped.append({"why": why, "tx": conn.execute(
                'select t.booking_date, t.amount_cents, m.name from "transaction" t'
                " left join member m on m.id=t.member_id where t.id=?", (tx_id,)).fetchone()})
    return render(request, "dues_confirm_result.html", res=res, skipped=skipped)


@router.get("/members/{member_id:int}/history")
async def member_history(request: Request, member_id: int, year: str | None = None,
                         conn=Depends(get_conn), today: date = Depends(get_today)):
    check_id(member_id)
    member = conn.execute("select * from member where id=?", (member_id,)).fetchone()
    if member is None:
        raise HTTPException(404)
    y = parse_year(year, today)
    if y is None:
        return year_error(request, f"/members/{member_id}/history")
    months = [month_status(conn, member_id, f"{y:04d}-{i:02d}", today) for i in range(1, 13)]
    return render(request, "member_history.html", member=member, year=y, months=months,
                  type_name=type_at(conn, member_id, month_of(today)) or "–",
                  payments=payment_history(conn, member_id), **year_nav(y))


def _quick_links(today: date) -> list[tuple[str, str]]:
    this = month_of(today)
    return [
        ("Ende dieses Monats", month_end(this).isoformat()),
        ("Ende nächsten Monats", month_end(add_months(this, 1)).isoformat()),
        ("Ende des Jahres", date(today.year, 12, 31).isoformat()),
        ("In 3 Monaten (Monatsende)", month_end(add_months(this, 3)).isoformat()),
    ]


@router.get("/forecast")
async def forecast_view(request: Request, until: str | None = None,
                        conn=Depends(get_conn), today: date = Depends(get_today)):
    default = month_end(month_of(today))
    ctx = {"quick": _quick_links(today), "until_text": until if until is not None
           else default.isoformat()}
    d = default if until is None else parse_date(until)
    if d is None:
        return render(request, "forecast.html", 422, error=E_DATE, fc=None, **ctx)
    try:
        fc = forecast(conn, d, today)
    except ValueError:
        return render(request, "forecast.html", 422, error=E_HORIZON, fc=None, **ctx)
    names = {m["id"]: m["name"] for m in conn.execute("select id, name from member")}
    items = [{"member_id": mid, "name": names.get(mid, "?"), "month": mo, "open": c}
             for mid, mo, c in fc.items]
    return render(request, "forecast.html", error=None, fc=fc, items=items, **ctx)
