"""Regeln auf Konten: Regel hinzufügen, Regeln neu anwenden. Konten legt der Kontenplan an."""
import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from kassivo import categories, ledger
from kassivo.money import parse_cents
from kassivo.web.common import MAX_CENTS, get_conn, parse_id, render

router = APIRouter()
MAX_CONTAINS, MAX_PRIORITY = 200, 10_000
KIND_TEXT = {"income": "Ertrag (Eingänge)", "expense": "Aufwand (Ausgänge)"}
FIELD_TEXT = {"purpose": "Verwendungszweck", "cp_name": "Name der Gegenpartei",
              "cp_iban": "IBAN der Gegenpartei"}


def _page(request, conn, status=200, errors=None, values=None, form=""):
    accounts = [dict(a, label=ledger.account_label(a)) for a in
                ledger.list_accounts(conn, kinds=("income", "expense"))]
    rules = conn.execute("select * from posting_rule order by priority, id").fetchall()
    by_cat: dict[int, list] = {}
    for r in rules:
        by_cat.setdefault(r["account_id"], []).append(r)
    # ausgeblendete Konten nur zeigen, wenn Regeln auf ihnen ruhen
    accounts = [a for a in accounts if not a["hidden"] or a["id"] in by_cat]
    cats = [a for a in accounts if not a["hidden"]]
    applied = request.query_params.get("applied", "")
    return render(
        request, "categories.html", status, cats=cats, accounts=accounts, by_cat=by_cat,
        errors=errors or [],
        values=values or {}, form=form, kind_text=KIND_TEXT, field_text=FIELD_TEXT,
        applied=int(applied) if re.fullmatch(r"[0-9]{1,9}", applied) else None,
        ok=request.query_params.get("ok") == "1",
    )


def _amount(text: str, label: str, errors: list) -> int | None:
    text = text.strip()
    if not text:
        return None
    try:
        cents = parse_cents(text)
    except ValueError:
        errors.append(f"{label}: Bitte einen Betrag im Format 12,50 angeben.")
        return None
    if not 0 <= cents <= MAX_CENTS:
        errors.append(f"{label}: Der Betrag muss zwischen 0,00 und 100.000,00 liegen.")
        return None
    return cents


@router.get("/categories")
async def categories_page(request: Request, conn=Depends(get_conn)):
    return _page(request, conn)


@router.post("/categories")
async def categories_submit(
    request: Request, form: str = Form(""),
    category_id: str = Form(""), field: str = Form(""), contains: str = Form(""),
    priority: str = Form(""), min_amount: str = Form(""), max_amount: str = Form(""),
    conn=Depends(get_conn),
):
    values = {"category_id": category_id, "field": field,
              "contains": contains, "priority": priority, "min_amount": min_amount,
              "max_amount": max_amount}
    errors: list[str] = []
    if form == "category":
        errors.append("Konten legen Sie im Kontenplan an.")
    elif form == "rule":
        cat = parse_id(category_id)
        if cat is None:
            errors.append("Bitte ein Konto wählen.")
        if field not in categories.FIELD_COLUMNS:
            errors.append("Bitte ein Feld wählen.")
        text = contains.strip()
        if not 1 <= len(text) <= MAX_CONTAINS:
            errors.append(f"Der Suchtext muss 1 bis {MAX_CONTAINS} Zeichen lang sein.")
        prio = 100
        if priority.strip():
            if re.fullmatch(r"[0-9]{1,5}", priority.strip()) and int(priority) <= MAX_PRIORITY:
                prio = int(priority)
            else:
                errors.append(f"Die Priorität muss eine Zahl von 0 bis {MAX_PRIORITY} sein.")
        lo = _amount(min_amount, "Mindestbetrag", errors)
        hi = _amount(max_amount, "Höchstbetrag", errors)
        if lo is not None and hi is not None and lo > hi:
            errors.append("Der Mindestbetrag darf nicht größer als der Höchstbetrag sein.")
        if not errors:
            try:
                categories.add_rule(conn, cat, field, text, priority=prio,
                                    min_cents=lo, max_cents=hi)
            except ValueError as e:
                errors.append(str(e))
    else:
        errors.append("Unbekanntes Formular.")
    if errors:
        conn.rollback()
        return _page(request, conn, 422, errors, values, form)
    conn.commit()
    return RedirectResponse("/categories?ok=1", status_code=303)


@router.post("/categories/apply")
async def categories_apply(request: Request, overwrite: str = Form(""),
                           confirm: str = Form(""), conn=Depends(get_conn)):
    over = overwrite == "on"
    if over and confirm != "yes":
        return _page(request, conn, 422, ["Zum Überschreiben bitte ausdrücklich bestätigen."],
                     {}, "apply")
    changed = categories.apply_rules(conn, None, overwrite=over)
    conn.commit()
    return RedirectResponse(f"/categories?applied={changed}", status_code=303)
