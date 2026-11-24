"""Buchungen: Liste, Kategorie, Mitglied, Unklar-Liste, Verteilung."""
import re
from datetime import date
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from kassivo import categories, dues, matching
from kassivo.iban import normalize_iban
from kassivo.ingest import mask_iban
from kassivo.members import list_members
from kassivo.money import parse_cents
from kassivo.web.common import (
    E_MONTH, MAX_CENTS, MAX_YEAR, MIN_YEAR, check_id, get_conn, parse_id, parse_month,
    render, safe_next, with_note,
)

router = APIRouter()
PAGE_SIZE = 100
MAX_PAGE = 100_000
MAX_Q = 200
EXTRA_ROWS = 3
MAX_ROWS = 200
STATUS_TEXT = {"NONE": "kein Beitrag fällig", "PAID": "bezahlt", "PARTIAL": "teilweise bezahlt",
               "OPEN": "offen", "OVERDUE": "überfällig"}
ALLOC_TEXT = {"proposed": "vorgeschlagen", "confirmed": "bestätigt"}
_ROW_KEY = re.compile(r"(month|amount)_([0-9]{1,3})", re.ASCII)


def _see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _error(request, message: str, status: int = 422, back="/transactions",
           back_label="Zu den Buchungen"):
    return render(request, "error.html", status, message=message, back=back,
                  back_label=back_label)


def _tx(conn, tx_id: int):
    check_id(tx_id)
    row = conn.execute('select * from "transaction" where id=?', (tx_id,)).fetchone()
    if row is None:
        raise HTTPException(404)
    return row


def _cats(conn):
    return conn.execute("select id, name from category order by name, id").fetchall()


def _cp(row) -> dict:
    iban = normalize_iban(row["cp_iban"]) if row["cp_iban"] else ""
    return {"tx": row, "iban": mask_iban(iban) if iban else ""}


# --- Liste -------------------------------------------------------------------

def _parse_filters(params) -> tuple[dict, str | None]:
    year = params.get("year", "").strip() or str(date.today().year)
    category = params.get("category", "").strip()
    q = params.get("q", "").strip()
    kind = params.get("kind", "").strip()
    page = params.get("page", "").strip() or "1"
    proposed = params.get("proposed", "").strip()
    f = {"year": year, "category": category, "q": q, "kind": kind, "proposed": proposed,
         "page": 1}
    if year != "all" and not (re.fullmatch(r"[0-9]{4}", year)
                              and MIN_YEAR <= int(year) <= MAX_YEAR):
        return f, f"Jahr muss „all“ oder eine Zahl von {MIN_YEAR} bis {MAX_YEAR} sein."
    if category not in ("", "none") and parse_id(category) is None:
        return f, "Ungültige Kategorie im Filter."
    if kind not in ("", "in", "out"):
        return f, "Art muss Einnahme, Ausgabe oder leer sein."
    if proposed not in ("", "1"):
        return f, "Filter für offene Vorschläge muss 1 oder leer sein."
    if len(q) > MAX_Q:
        return f, f"Der Suchtext darf höchstens {MAX_Q} Zeichen lang sein."
    if not re.fullmatch(r"[0-9]{1,6}", page) or not 1 <= int(page) <= MAX_PAGE:
        return f, f"Seite muss eine Zahl von 1 bis {MAX_PAGE} sein."
    f["page"] = int(page)
    return f, None


def _where(f: dict) -> tuple[str, list]:
    """WHERE nur aus festen Fragmenten; Nutzerwerte stehen ausschließlich in params."""
    parts, params = [], []
    if f["year"] != "all":
        parts.append("t.booking_date >= ? and t.booking_date < ?")
        params += [f"{int(f['year']):04d}-01-01", f"{int(f['year']) + 1:04d}-01-01"]
    if f["category"] == "none":
        parts.append("t.category_id is null")
    elif f["category"]:
        parts.append("t.category_id = ?")
        params.append(int(f["category"]))
    if f["kind"] == "in":
        parts.append("t.amount_cents > 0")
    elif f["kind"] == "out":
        parts.append("t.amount_cents < 0")
    if f["proposed"] == "1":
        parts.append("exists (select 1 from allocation a"
                     " where a.tx_id = t.id and a.status = 'proposed')")
    if f["q"]:
        parts.append("(instr(cf(t.cp_name), ?) > 0 or instr(cf(t.purpose), ?) > 0)")
        params += [f["q"].casefold()] * 2
    return (" where " + " and ".join(parts)) if parts else "", params


def _casefold_fn(conn) -> None:
    conn.create_function("cf", 1, lambda s: s.casefold() if isinstance(s, str) else "",
                         deterministic=True)


@router.get("/transactions")
async def transactions(request: Request, conn=Depends(get_conn)):
    f, err = _parse_filters(request.query_params)
    if err:
        return _error(request, err, back="/transactions")
    _casefold_fn(conn)
    where, params = _where(f)
    count, income, expense = conn.execute(
        "select count(*), coalesce(sum(case when t.amount_cents > 0 then t.amount_cents end), 0),"
        " coalesce(sum(case when t.amount_cents < 0 then t.amount_cents end), 0)"
        f' from "transaction" t{where}', params).fetchone()
    rows = conn.execute(
        'select t.*, m.name as member_name from "transaction" t'
        f" left join member m on m.id = t.member_id{where}"
        " order by t.booking_date desc, t.id desc limit ? offset ?",
        params + [PAGE_SIZE, (f["page"] - 1) * PAGE_SIZE]).fetchall()
    base = {k: f[k] for k in ("year", "category", "q", "kind", "proposed") if f[k]}

    def page_url(p: int) -> str:
        return "/transactions?" + urlencode({**base, "page": p})

    pages = max(1, -(-count // PAGE_SIZE))
    here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return render(
        request, "transactions.html", f=f, rows=[_cp(r) for r in rows], count=count,
        income=income, expense=expense, cats=_cats(conn), members=list_members(conn),
        next_url=safe_next(here), pages=pages,
        prev_url=page_url(f["page"] - 1) if f["page"] > 1 else None,
        next_page_url=page_url(f["page"] + 1) if f["page"] < pages else None,
    )


@router.get("/unmatched")
async def unmatched(request: Request, conn=Depends(get_conn)):
    return render(request, "unmatched.html", rows=[_cp(r) for r in matching.unmatched(conn)],
                  members=list_members(conn), next_url="/unmatched",
                  cats=conn.execute("select id, name from category where kind in ('in', 'both')"
                                    " order by name, id").fetchall())


@router.post("/unmatched/auto")
async def unmatched_auto(conn=Depends(get_conn)):
    """Regeln und Zuordnung erneut anwenden: gelernte und Stamm-IBANs auf "Unklar"."""
    n = matching.auto_assign(conn, [r["id"] for r in matching.unmatched(conn)])
    return _see_other(with_note("/unmatched", "auto", n))


# --- Kategorie und Mitglied ----------------------------------------------------

@router.post("/transactions/{tx_id:int}/category")
async def set_category(request: Request, tx_id: int, category_id: str = Form(""),
                       next: str = Form(""), conn=Depends(get_conn)):
    _tx(conn, tx_id)
    cat = None
    if category_id.strip():
        cat = parse_id(category_id)
        if cat is None:
            return _error(request, "Unbekannte Kategorie.", back=safe_next(next))
    try:
        categories.set_category(conn, tx_id, cat)
    except ValueError as e:
        return _error(request, str(e), back=safe_next(next))
    conn.commit()
    return _see_other(safe_next(next))


@router.post("/transactions/{tx_id:int}/member")
async def set_member(request: Request, tx_id: int, member_id: str = Form(""),
                     learn: str = Form(""), next: str = Form(""), conn=Depends(get_conn)):
    _tx(conn, tx_id)
    member = None
    if member_id.strip():
        member = parse_id(member_id)
        if member is None:
            return _error(request, "Unbekanntes Mitglied.", back=safe_next(next))
    learning = learn == "on"
    try:
        matching.assign_member(conn, tx_id, member, learn=learning)
    except ValueError as e:
        return _error(request, str(e), back=safe_next(next))
    if not (learning and member is not None):
        return _see_other(safe_next(next))
    # Gelernte IBAN gleich auf die übrigen unklaren Zahlungen dieses Absenders
    # anwenden; ist sie mehrdeutig geworden, wird nichts weiter zugeordnet und
    # der Hinweis sagt das.
    iban = normalize_iban(_tx(conn, tx_id)["cp_iban"])
    if matching.iban_is_ambiguous(conn, iban):
        return _see_other(with_note(safe_next(next), "ambiguous", 0))
    others = [r["id"] for r in matching.unmatched(conn)
              if iban and normalize_iban(r["cp_iban"]) == iban]
    n = matching.auto_assign(conn, others)
    target = safe_next(next)
    return _see_other(with_note(target, "learned", n) if n else target)


# --- Verteilung ----------------------------------------------------------------

def _cents_text(cents: int) -> str:
    return f"{cents // 100},{cents % 100:02d}"


def _alloc_tx(conn, tx_id: int):
    """(Buchung, Mitglied, Fehlertext). 404 nur bei fehlender Buchung."""
    tx = _tx(conn, tx_id)
    if tx["amount_cents"] <= 0:
        return tx, None, "Eine Verteilung gibt es nur für Zahlungseingänge."
    if tx["member_id"] is None:
        return tx, None, "Die Buchung ist keinem Mitglied zugeordnet."
    member = conn.execute("select * from member where id=?", (tx["member_id"],)).fetchone()
    return tx, member, None


def _alloc_page(request, conn, tx, member, status=200, rows=None, errors=None, nxt=""):
    stored = conn.execute(
        "select month, cents, status from allocation where tx_id=? order by month, id",
        (tx["id"],)).fetchall()
    by_month = {r["month"]: r for r in stored}
    if rows is None:
        rows = [{"month": r["month"], "amount": _cents_text(r["cents"])} for r in stored]
    rows = [dict(r) for r in rows]
    empties = sum(1 for r in rows if not r["month"] and not r["amount"])
    rows += [{"month": "", "amount": ""} for _ in range(max(0, EXTRA_ROWS - empties))]
    today, total = date.today(), 0
    for r in rows:
        r["alloc"] = ALLOC_TEXT[by_month[r["month"]]["status"]] if r["month"] in by_month else "–"
        r["info"], r["stale"] = None, False
        if parse_month(r["month"]) is not None:
            ms = dues.month_status(conn, member["id"], r["month"].strip(), today)
            r["info"] = {"text": STATUS_TEXT[ms.status], "expected": ms.expected, "paid": ms.paid}
            stored_row = by_month.get(r["month"].strip())
            r["stale"] = (ms.status == "PAID" and stored_row is not None
                          and stored_row["status"] == "proposed")
        try:
            total += parse_cents(r["amount"])
        except ValueError:
            pass
    return render(request, "allocate.html", status, tx=tx, member=member, rows=rows,
                  stored=stored, total=total, diff=tx["amount_cents"] - total,
                  errors=errors or [], next_value=safe_next(nxt), cp=_cp(tx))


def _allocate_url(tx_id: int, nxt: str) -> str:
    url = f"/transactions/{tx_id}/allocate"
    safe = safe_next(nxt, "")
    return f"{url}?next={quote(safe, safe='')}" if safe else url


@router.get("/transactions/{tx_id:int}/allocate")
async def allocate_form(request: Request, tx_id: int, conn=Depends(get_conn)):
    tx, member, err = _alloc_tx(conn, tx_id)
    if err:
        return _error(request, err)
    return _alloc_page(request, conn, tx, member, nxt=request.query_params.get("next", ""))


@router.post("/transactions/{tx_id:int}/allocate")
async def allocate_submit(request: Request, tx_id: int, conn=Depends(get_conn)):
    tx, member, err = _alloc_tx(conn, tx_id)
    if err:
        return _error(request, err)
    form = await request.form()
    nxt = form.get("next", "")
    nxt = nxt if isinstance(nxt, str) else ""
    raw: dict[int, dict] = {}
    for key, value in form.multi_items():
        m = _ROW_KEY.fullmatch(key)
        if m and isinstance(value, str):
            raw.setdefault(int(m[2]), {"month": "", "amount": ""})[m[1]] = value.strip()
    if len(raw) > MAX_ROWS:
        return _alloc_page(request, conn, tx, member, 422, [], ["Zu viele Zeilen."], nxt)
    rows = [raw[i] for i in sorted(raw)]
    errors, pairs, seen = [], [], set()
    for n, r in enumerate((r for r in rows if r["month"] or r["amount"]), start=1):
        month = parse_month(r["month"])
        try:
            cents = parse_cents(r["amount"])
        except ValueError:
            cents = None
        if month is None:
            errors.append(f"Zeile {n}: {E_MONTH}")
        if cents is None or not 1 <= cents <= MAX_CENTS:
            errors.append(f"Zeile {n}: Betrag muss zwischen 0,01 und 100.000,00 liegen "
                          "(Format 12,50).")
        if month is not None and month in seen:
            errors.append(f"Zeile {n}: Monat {month} kommt doppelt vor.")
        if month is None or cents is None or not 1 <= cents <= MAX_CENTS:
            continue
        seen.add(month)
        pairs.append((month, cents))
    if not errors:
        try:
            dues.confirm_allocations(conn, tx_id, pairs)
        except ValueError as e:
            errors.append(str(e))
    if errors:
        return _alloc_page(request, conn, tx, member, 422, rows, errors, nxt)
    return _see_other(safe_next(nxt))


@router.post("/transactions/{tx_id:int}/allocate/propose")
async def allocate_propose(request: Request, tx_id: int, next: str = Form(""),
                           conn=Depends(get_conn)):
    tx, member, err = _alloc_tx(conn, tx_id)
    if err:
        return _error(request, err)
    try:
        dues.propose_allocations(conn, tx_id)
    except ValueError as e:
        return _alloc_page(request, conn, tx, member, 422, errors=[str(e)], nxt=next)
    return _see_other(_allocate_url(tx_id, next))


@router.post("/transactions/{tx_id:int}/allocate/clear")
async def allocate_clear(request: Request, tx_id: int, confirm: str = Form(""),
                         next: str = Form(""), conn=Depends(get_conn)):
    tx, member, err = _alloc_tx(conn, tx_id)
    if err:
        return _error(request, err)
    if confirm != "yes":
        return _alloc_page(request, conn, tx, member, 422, nxt=next, errors=[
            "Bitte bestätigen Sie, dass die Verteilung aufgehoben wird."])
    try:
        dues.clear_allocations(conn, tx_id)
    except ValueError as e:
        return _alloc_page(request, conn, tx, member, 422, errors=[str(e)], nxt=next)
    return _see_other(_allocate_url(tx_id, next))
