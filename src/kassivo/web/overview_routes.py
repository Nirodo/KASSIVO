"""Startseite „Heute zu tun“. Nur lesend."""
from datetime import date

from fastapi import APIRouter, Depends, Request

from kassivo.dues import check_start_is_set
from kassivo.overview import overview
from kassivo.web.common import MIN_YEAR, get_conn, get_today, render

router = APIRouter()
MONTH_NAMES = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
               "September", "Oktober", "November", "Dezember")


@router.get("/")
async def overview_view(request: Request, conn=Depends(get_conn),
                        today: date = Depends(get_today)):
    return render(request, "overview.html", o=overview(conn, today, MIN_YEAR),
                  check_start_set=check_start_is_set(conn), year=today.year,
                  month_name=MONTH_NAMES[today.month - 1],
                  today_text=today.strftime("%d.%m.%Y"))
