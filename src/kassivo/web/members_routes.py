"""Mitglieder: Liste, Anlegen, Bearbeiten, Mitgliedsart, Austritt."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from kassivo.iban import normalize_iban
from kassivo.ingest import mask_iban
from kassivo.members import (
    create_member, leave_member, list_members, set_member_type, type_at, update_member,
)
from kassivo.web.common import (
    E_DATE, E_MONTH, current_month, get_conn, parse_date, parse_month, render, type_names,
)

router = APIRouter()
STAMM = ("name", "street", "zip", "city", "email", "phone", "iban")


def _stamm(name, street, zip_, city, email, phone, iban) -> dict:
    return dict(zip(STAMM, (name, street, zip_, city, email, phone, iban)))


def _get(conn, member_id: int):
    row = conn.execute("select * from member where id=?", (member_id,)).fetchone()
    if row is None:
        raise HTTPException(404)
    return row


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


@router.get("/members")
async def members(request: Request, q: str = "", conn=Depends(get_conn)):
    month, needle = current_month(), q.strip().casefold()
    rows = [
        {"m": m, "type": type_at(conn, m["id"], month) or "–",
         "iban": mask_iban(normalize_iban(m["iban"])) if m["iban"] else ""}
        for m in list_members(conn)
        if needle in m["name"].casefold()
    ]
    return render(request, "members.html", rows=rows, q=q)


def _new_form(request, conn, status=200, values=None, errors=None):
    return render(request, "member_form.html", status, member=None,
                  values=values or {}, errors=errors or {}, types=type_names(conn))


@router.get("/members/new")
async def new_form(request: Request, conn=Depends(get_conn)):
    return _new_form(request, conn)


@router.post("/members/new")
async def new_submit(
    request: Request, name: str = Form(""), type_name: str = Form(""),
    eintritt: str = Form(""), street: str = Form(""), zip: str = Form(""),
    city: str = Form(""), email: str = Form(""), phone: str = Form(""),
    iban: str = Form(""), fee_start: str = Form(""), conn=Depends(get_conn),
):
    values = {**_stamm(name, street, zip, city, email, phone, iban),
              "type_name": type_name, "eintritt": eintritt, "fee_start": fee_start}
    errors = {}
    if not name.strip():
        errors["name"] = "Name ist erforderlich."
    if type_name not in type_names(conn):
        errors["type_name"] = "Bitte eine Mitgliedsart wählen."
    d = parse_date(eintritt)
    if d is None:
        errors["eintritt"] = E_DATE
    start = None
    if fee_start.strip():
        start = parse_month(fee_start)
        if start is None:
            errors["fee_start"] = E_MONTH
    if errors:
        return _new_form(request, conn, 422, values, errors)
    create_member(conn, name.strip(), type_name, d, street=street.strip(),
                  zip_code=zip.strip(), city=city.strip(), email=email.strip(),
                  phone=phone.strip(), iban=iban, fee_start=start)
    return _redirect("/members")


def _edit_form(request, conn, member, status=200, values=None, errors=None,
               type_values=None, leave_value="", form=""):
    return render(
        request, "member_form.html", status, member=member,
        values=values or {k: member[k] or "" for k in STAMM}, errors=errors or {},
        types=type_names(conn), current_type=type_at(conn, member["id"], current_month()),
        type_values=type_values or {}, leave_value=leave_value, form=form,
        ok=request.query_params.get("ok") == "1",
    )


@router.get("/members/{member_id:int}")
async def edit_form(request: Request, member_id: int, conn=Depends(get_conn)):
    return _edit_form(request, conn, _get(conn, member_id))


@router.post("/members/{member_id:int}")
async def edit_submit(
    request: Request, member_id: int, name: str = Form(""), street: str = Form(""),
    zip: str = Form(""), city: str = Form(""), email: str = Form(""),
    phone: str = Form(""), iban: str = Form(""), conn=Depends(get_conn),
):
    member = _get(conn, member_id)
    if not name.strip():
        return _edit_form(request, conn, member, 422,
                          _stamm(name, street, zip, city, email, phone, iban),
                          {"name": "Name ist erforderlich."}, form="stamm")
    update_member(conn, member_id, name=name.strip(), street=street.strip(),
                  zip=zip.strip(), city=city.strip(), email=email.strip(),
                  phone=phone.strip(), iban=iban)
    return _redirect(f"/members/{member_id}?ok=1")


@router.post("/members/{member_id:int}/type")
async def change_type(
    request: Request, member_id: int, type_name: str = Form(""),
    valid_from: str = Form(""), conn=Depends(get_conn),
):
    member = _get(conn, member_id)
    errors = {}
    if type_name not in type_names(conn):
        errors["type_name"] = "Bitte eine Mitgliedsart wählen."
    month = parse_month(valid_from)
    if month is None:
        errors["valid_from"] = E_MONTH
    if errors:
        return _edit_form(request, conn, member, 422, errors=errors, form="type",
                          type_values={"type_name": type_name, "valid_from": valid_from})
    set_member_type(conn, member_id, type_name, month)
    return _redirect(f"/members/{member_id}?ok=1")


@router.post("/members/{member_id:int}/leave")
async def leave(
    request: Request, member_id: int, austritt: str = Form(""), conn=Depends(get_conn),
):
    member = _get(conn, member_id)
    d = parse_date(austritt)
    if d is None:
        return _edit_form(request, conn, member, 422, errors={"austritt": E_DATE},
                          leave_value=austritt, form="leave")
    leave_member(conn, member_id, d)
    return _redirect(f"/members/{member_id}?ok=1")
