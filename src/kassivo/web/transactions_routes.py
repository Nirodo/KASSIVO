"""Buchungen: Liste, Kategorie, Mitglied, Unklar-Liste, Verteilung."""
import re
from datetime import date
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from kassivo import dues, ledger, matching
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


def _cats(conn, kinds=("income", "expense")):
    """Auswahl „Kategorie“ bis Schritt 3: sichtbare Ertrags- und Aufwandskonten."""
    return [{"id": a["id"], "name": ledger.account_label(a)}
            for a in ledger.list_accounts(conn, visible_only=True, kinds=kinds)]


def _hidden_counters(conn, rows, cats) -> dict[int, dict]:
    """Gegenkonten der Zeilen, die nicht in der Auswahl stehen (ausgeblendet): als zusätzliche
    Option, sonst zeigte die Auswahl ein falsches Konto."""
    shown = {c["id"] for c in cats}
    ids = sorted({r["counter_id"] for r in rows
                  if r["counter_id"] is not None and r["counter_id"] not in shown})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {a["id"]: {"id": a["id"], "name": ledger.account_label(a) + " (ausgeblendet)"}
            for a in conn.execute(f"select id, number, name from ledger_account"
                                  f" where id in ({marks})", ids)}


def _cp(row) -> dict:
    iban = normalize_iban(row["cp_iban"]) if row["cp_iban"] else ""
    return {"tx": row, "iban": mask_iban(iban) if iban else ""}


def _remember_change(request, conn) -> dict | None:
    """Hinweis nach einem Wechsel des gemerkten Mitglieds (#55, Fix-Runde I2).

    Nur aus geprüften IDs: die Zahlung gibt es, ihr Mitglied ist das jetzt
    gemerkte, das vorherige Mitglied gibt es und ist ein anderes. Sonst None,
    dann bleibt der allgemeine Hinweis. Namen und IBAN stehen nie in der URL.
    """
    q = request.query_params
    if q.get("note") not in ("changed", "changed_r"):
        return None
    tx_id, prev = parse_id(q.get("tx", "")), parse_id(q.get("prev", ""))
    if tx_id is None or prev is None:
        return None
    tx = conn.execute('select member_id, cp_iban from "transaction" where id=?',
                      (tx_id,)).fetchone()
    iban = normalize_iban(tx["cp_iban"]) if tx and tx["cp_iban"] else None
    if not iban or tx["member_id"] is None or tx["member_id"] == prev:
        return None
    entry = conn.execute("select rowid as id, iban, member_id from member_iban where iban=?",
                         (iban,)).fetchone()
    if entry is None or entry["member_id"] != tx["member_id"]:
        return None
    names = dict(conn.execute("select id, name from member where id in (?, ?)",
                              (prev, tx["member_id"])).fetchall())
    if prev not in names:
        return None
    raw = q.get("n", "")
    moved = int(raw) if q.get("note") == "changed_r" and re.fullmatch(r"[0-9]{1,7}", raw) else 0
    return {"entry_id": entry["id"], "check": matching.entry_check(entry["iban"]),
            "new_name": names[tx["member_id"]], "prev_name": names[prev], "moved": moved}


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
        parts.append("not " + ledger.BOOKED)
    elif f["category"]:
        parts.append(ledger.ON_ACCOUNT)
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
        f"select t.*, m.name as member_name, {ledger.COUNTER} as counter_id,"
        f' {ledger.BOOKED} as booked from "transaction" t'
        f" left join member m on m.id = t.member_id{where}"
        " order by t.booking_date desc, t.id desc limit ? offset ?",
        params + [PAGE_SIZE, (f["page"] - 1) * PAGE_SIZE]).fetchall()
    base = {k: f[k] for k in ("year", "category", "q", "kind", "proposed") if f[k]}

    def page_url(p: int) -> str:
        return "/transactions?" + urlencode({**base, "page": p})

    pages = max(1, -(-count // PAGE_SIZE))
    # Hinweis-Parameter nicht in Rücksprung-Links übernehmen (sonst erscheint er erneut)
    query = request.url.query
    if "note" in request.query_params:
        query = urlencode([(k, v) for k, v in request.query_params.multi_items()
                           if k not in ("note", "n", "a", "r", "o", "tx", "prev")])
    here = request.url.path + (f"?{query}" if query else "")
    reasons = ({r["id"]: dues.review_reason(conn, r["id"]) for r in rows}
               if f["proposed"] == "1" else {})
    cats = _cats(conn)
    return render(
        request, "transactions.html", f=f, rows=[_cp(r) for r in rows], count=count,
        income=income, expense=expense, cats=cats, hidden=_hidden_counters(conn, rows, cats),
        members=list_members(conn),
        reasons=reasons, remember_change=_remember_change(request, conn),
        next_url=safe_next(here), pages=pages,
        prev_url=page_url(f["page"] - 1) if f["page"] > 1 else None,
        next_page_url=page_url(f["page"] + 1) if f["page"] < pages else None,
    )


@router.get("/unmatched")
async def unmatched(request: Request, conn=Depends(get_conn)):
    return render(request, "unmatched.html", rows=[_cp(r) for r in matching.unmatched(conn)],
                  members=list_members(conn), next_url="/unmatched",
                  remember_change=_remember_change(request, conn),
                  cats=_cats(conn, ("income",)))


@router.post("/unmatched/auto")
async def unmatched_auto(conn=Depends(get_conn)):
    """Regeln und Zuordnung erneut anwenden: gelernte und Stamm-IBANs auf "Unklar"."""
    n = matching.auto_assign(conn, [r["id"] for r in matching.unmatched(conn)])
    return _see_other(with_note("/unmatched", "auto", n))


# --- Kategorie und Mitglied ----------------------------------------------------

@router.post("/transactions/{tx_id:int}/category")
async def set_category(request: Request, tx_id: int, category_id: str = Form(""),
                       next: str = Form(""), conn=Depends(get_conn)):
    """Kategorie = Gegenkonto (bis Schritt 3). Bucht ins Journal; gleiche Auswahl noch einmal
    schreibt nichts, leer storniert die gültige Buchung."""
    _tx(conn, tx_id)
    account = None
    if category_id.strip():
        account = parse_id(category_id)
        row = None if account is None else conn.execute(
            "select kind from ledger_account where id=?", (account,)).fetchone()
        if row is None or row["kind"] not in ("income", "expense"):
            return _error(request, "Unbekannte Kategorie. Bitte ein Ertrags- oder "
                          "Aufwandskonto wählen.", back=safe_next(next))
    try:
        ledger.set_counter_account(conn, tx_id, account, source="manual")
    except ValueError as e:  # LedgerError, PeriodError: deutsche Meldung ohne Personendaten
        conn.rollback()
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
    prev = matching.effective_member(conn, _tx(conn, tx_id)["cp_iban"])
    before = dues.auto_snapshot(conn)
    try:
        learned = matching.assign_member(conn, tx_id, member, learn=learning)
    except ValueError as e:
        return _error(request, str(e), back=safe_next(next))
    moved = dues.count_changed(before, dues.auto_snapshot(conn), exclude=tx_id)
    if learned is None:
        target = safe_next(next)
        return _see_other(with_note(target, "recomputed_other", moved) if moved else target)
    if learned == "multiple":
        return _see_other(with_note(safe_next(next), "ambiguous_r", moved) if moved
                          else with_note(safe_next(next), "ambiguous", 0))
    if learned == "changed":
        # Gemerkt, aber keine weiteren Zahlungen zuordnen (#55): zahlt die
        # Eltern-IBAN für zwei Kinder, würde sonst still alles auf das zuletzt
        # gewählte Kind gebucht. Der Hinweis nennt beide Mitglieder (Namen nur
        # serverseitig aus den IDs, nie in der URL) und bietet "zahlt für mehrere".
        url = (with_note(safe_next(next), "changed_r", moved) if moved
               else with_note(safe_next(next), "changed", 0))
        return _see_other(f"{url}&tx={int(tx_id)}&prev={int(prev)}" if prev else url)
    # Neu oder unverändert gemerkte IBAN gleich auf die übrigen unklaren
    # Zahlungen dieses Absenders anwenden.
    iban = normalize_iban(_tx(conn, tx_id)["cp_iban"])
    others = [r["id"] for r in matching.unmatched(conn)
              if iban and normalize_iban(r["cp_iban"]) == iban]
    n = matching.auto_assign(conn, others)
    target = safe_next(next)
    if n:
        return _see_other(with_note(target, "learned_r", n, moved) if moved
                          else with_note(target, "learned", n))
    return _see_other(with_note(target, "recomputed_other", moved) if moved else target)


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
        "select month, cents, status, source from allocation where tx_id=? order by month, id",
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
    before = dues.auto_snapshot(conn, tx["member_id"])
    if not errors:
        try:
            dues.confirm_allocations(conn, tx_id, pairs)
        except ValueError as e:
            errors.append(str(e))
    if errors:
        return _alloc_page(request, conn, tx, member, 422, rows, errors, nxt)
    moved = dues.count_changed(before, dues.auto_snapshot(conn, tx["member_id"]), exclude=tx_id)
    target = safe_next(nxt)
    return _see_other(with_note(target, "recomputed_other", moved) if moved else target)


@router.post("/transactions/{tx_id:int}/allocate/propose")
async def allocate_propose(request: Request, tx_id: int, next: str = Form(""),
                           conn=Depends(get_conn)):
    tx, member, err = _alloc_tx(conn, tx_id)
    if err:
        return _error(request, err)
    try:
        dues.propose_allocations(conn, tx_id, auto=False)  # nur Vorschlag, nie bestätigt
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
    before = dues.auto_snapshot(conn, tx["member_id"])
    try:
        dues.clear_allocations(conn, tx_id)
    except ValueError as e:
        return _alloc_page(request, conn, tx, member, 422, errors=[str(e)], nxt=next)
    moved = dues.count_changed(before, dues.auto_snapshot(conn, tx["member_id"]), exclude=tx_id)
    url = _allocate_url(tx_id, next)
    return _see_other(with_note(url, "recomputed_other", moved) if moved else url)
