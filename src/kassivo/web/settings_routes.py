"""Beiträge je Art und Prüfungsstart."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from kassivo.dues import (
    check_start_is_set, get_check_start, repropose_pending, set_check_start,
)
from kassivo.fees import fee_at, set_fee
from kassivo.money import parse_cents
from kassivo.web.common import (
    E_MONTH, current_month, get_conn, parse_month, render, type_names, with_note,
)

router = APIRouter()
MAX_FEE_CENTS = 10_000_000


def _page(request, conn, status=200, errors=None, values=None, form=""):
    month, types = current_month(), type_names(conn)
    history = conn.execute(
        "select t.name, f.cents, f.valid_from from fee_rate f "
        "join membership_type t on t.id=f.type_id order by f.valid_from desc, t.id"
    ).fetchall()
    return render(
        request, "settings_fees.html", status, errors=errors or {}, values=values or {},
        form=form, types=types, month=month, history=history,
        current=[(t, fee_at(conn, t, month)) for t in types],
        check_start=get_check_start(conn), check_start_set=check_start_is_set(conn),
        ok=request.query_params.get("ok") == "1",
    )


@router.get("/settings/fees")
async def fees(request: Request, conn=Depends(get_conn)):
    return _page(request, conn)


@router.post("/settings/fees")
async def fees_submit(
    request: Request, form: str = Form(""), type_name: str = Form(""),
    amount: str = Form(""), valid_from: str = Form(""), check_start: str = Form(""),
    conn=Depends(get_conn),
):
    errors = {}
    if form == "check_start":
        month = parse_month(check_start)
        if month is None:
            errors["check_start"] = E_MONTH
            return _page(request, conn, 422, errors, {"check_start": check_start}, form)
        set_check_start(conn, month)
        # Vorschläge hingen am alten Start; Bestätigtes bleibt, wie es ist
        n = repropose_pending(conn)
        return RedirectResponse(with_note("/settings/fees", "reproposed", n), status_code=303)
    values = {"type_name": type_name, "amount": amount, "valid_from": valid_from}
    cents = 0
    try:
        cents = parse_cents(amount)
    except ValueError:
        pass
    if not 1 <= cents <= MAX_FEE_CENTS:
        errors["amount"] = "Bitte einen Betrag zwischen 0,01 und 100.000,00 angeben, z. B. 17,00."
    if type_name not in type_names(conn):
        errors["type_name"] = "Bitte eine Mitgliedsart wählen."
    month = parse_month(valid_from)
    if month is None:
        errors["valid_from"] = E_MONTH
    if errors:
        return _page(request, conn, 422, errors, values, "fee")
    set_fee(conn, type_name, cents, month)
    return RedirectResponse("/settings/fees?ok=1", status_code=303)
