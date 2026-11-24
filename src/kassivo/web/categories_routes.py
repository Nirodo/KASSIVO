"""Kategorien und Regeln: Anlegen, Regel hinzufügen, Regeln neu anwenden."""
import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from kassivo import categories
from kassivo.money import parse_cents
from kassivo.web.common import MAX_CENTS, get_conn, parse_id, render

router = APIRouter()
MAX_NAME, MAX_CONTAINS, MAX_PRIORITY = 100, 200, 10_000
KIND_TEXT = {"in": "Einnahmen", "out": "Ausgaben", "both": "Beide"}
FIELD_TEXT = {"purpose": "Verwendungszweck", "cp_name": "Name der Gegenpartei",
              "cp_iban": "IBAN der Gegenpartei"}


def _page(request, conn, status=200, errors=None, values=None, form=""):
    cats = conn.execute("select * from category order by name, id").fetchall()
    rules = conn.execute("select * from category_rule order by priority, id").fetchall()
    by_cat: dict[int, list] = {}
    for r in rules:
        by_cat.setdefault(r["category_id"], []).append(r)
    applied = request.query_params.get("applied", "")
    return render(
        request, "categories.html", status, cats=cats, by_cat=by_cat, errors=errors or [],
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
    request: Request, form: str = Form(""), name: str = Form(""), kind: str = Form(""),
    category_id: str = Form(""), field: str = Form(""), contains: str = Form(""),
    priority: str = Form(""), min_amount: str = Form(""), max_amount: str = Form(""),
    conn=Depends(get_conn),
):
    values = {"name": name, "kind": kind, "category_id": category_id, "field": field,
              "contains": contains, "priority": priority, "min_amount": min_amount,
              "max_amount": max_amount}
    errors: list[str] = []
    if form == "category":
        if not 1 <= len(name.strip()) <= MAX_NAME:
            errors.append(f"Der Name muss 1 bis {MAX_NAME} Zeichen lang sein.")
        if kind not in categories.KINDS:
            errors.append("Bitte Einnahmen, Ausgaben oder Beide wählen.")
        if not errors:
            try:
                categories.create_category(conn, name.strip(), kind)
            except ValueError as e:
                errors.append(str(e))
    elif form == "rule":
        cat = parse_id(category_id)
        if cat is None:
            errors.append("Bitte eine Kategorie wählen.")
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
