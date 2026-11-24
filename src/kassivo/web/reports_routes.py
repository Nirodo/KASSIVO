"""Jahresreport mit Diagrammen. Nur lesend."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from kassivo.reports import year_summary
from kassivo.web.common import (
    E_YEAR, get_conn, get_today, parse_year, render, year_error, year_nav,
)

router = APIRouter()


@router.get("/reports")
async def reports_view(request: Request, year: str | None = None,
                       conn=Depends(get_conn), today: date = Depends(get_today)):
    y = parse_year(year, today)
    if y is None:
        return year_error(request, "/reports")
    s = year_summary(conn, y)
    empty = s["income_cents"] == 0 and s["expense_cents"] == 0
    return render(request, "reports.html", year=y, s=s, empty=empty, **year_nav(y))


@router.get("/reports/data")
async def reports_data(year: str | None = None, conn=Depends(get_conn),
                       today: date = Depends(get_today)):
    y = parse_year(year, today)
    if y is None:
        return JSONResponse({"error": E_YEAR}, status_code=422)
    return JSONResponse({**year_summary(conn, y), "year": y})
