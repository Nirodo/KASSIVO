"""Beitragsübersicht, Zahlungshistorie je Mitglied, Prognose. Nur lesend."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request

from kassivo.dues import dues_grid, month_status, payment_history
from kassivo.forecast import forecast
from kassivo.members import type_at
from kassivo.months import add_months, month_end, month_of
from kassivo.web.common import (
    E_DATE, check_id, get_conn, get_today, parse_date, parse_year, render, year_error, year_nav,
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
    totals = [
        {"expected": sum(r["months"][i].expected for r in grid),
         "paid": sum(r["months"][i].paid for r in grid)}
        for i in range(12)
    ]
    return render(request, "dues.html", year=y, grid=grid, totals=totals, **year_nav(y))


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
