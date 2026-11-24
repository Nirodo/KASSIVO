"""Prognose offener Mitgliedsbeiträge (v1: keine wiederkehrenden Ausgaben).

Beträge sind int-Cent. Grundlage ist month_status: nur bestätigte
Verteilungen zählen als bezahlt, Vorschläge senken die Prognose nicht.
"""
from dataclasses import dataclass
from datetime import date, datetime

from kassivo.dues import get_check_start, month_status
from kassivo.members import list_members
from kassivo.months import month_of, month_range


@dataclass(frozen=True)
class Forecast:
    overdue_cents: int
    due_cents: int
    items: list[tuple[int, str, int]]  # (member_id, month, open_cents)


def _is_date(d) -> bool:
    return isinstance(d, date) and not isinstance(d, datetime)


def forecast(conn, until: date, today: date) -> Forecast:
    """Offene Beiträge vom Prüfungsstart bis einschließlich Monat von until.

    OVERDUE (vergangene Monate) geht in overdue_cents, OPEN/PARTIAL
    (laufender und künftige Monate) in due_cents. NONE/PAID zählen nicht.
    """
    if not _is_date(until) or not _is_date(today):
        raise ValueError("until und today müssen datetime.date sein")
    members = list_members(conn)
    starts = [m["fee_start"] for m in members if m["fee_start"]]
    start = get_check_start(conn) or (min(starts) if starts else None)
    if not members or start is None:
        return Forecast(0, 0, [])

    overdue = due = 0
    items = []
    for month in month_range(start, month_of(until)):
        for member_id in sorted(m["id"] for m in members):
            st = month_status(conn, member_id, month, today)
            if st.status in ("NONE", "PAID"):
                continue
            open_cents = st.expected - st.paid
            if st.status == "OVERDUE":
                overdue += open_cents
            else:
                due += open_cents
            items.append((member_id, month, open_cents))
    return Forecast(overdue, due, items)
