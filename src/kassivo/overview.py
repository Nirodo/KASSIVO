"""Zahlen der Startseite „Heute zu tun“ (#48). Nur lesend, Beträge in Cent.

Jede Zahl kommt aus derselben Funktion wie ihre Zielseite, damit Zähler und
Zielseite übereinstimmen. Keine eigene Geldrechnung, keine eigene Zählabfrage.

Verworfen: eigene count-Abfragen (schneller, laufen aber still auseinander) und
die Rückstände aus forecast (eine Summe über alle Jahre, die keine Seite so
zählt; /dues zeigt immer nur ein Jahr).
"""
from datetime import date

from kassivo import dues, matching
from kassivo.members import list_members
from kassivo.months import month_of
from kassivo.reports import year_summary


def month_totals(grid: list[dict], index: int) -> dict:
    """Fußzeile des Beitragsrasters für den Monat index (0 = Januar)."""
    return {"expected": sum(r["months"][index].expected for r in grid),
            "paid": sum(r["months"][index].paid for r in grid)}


def _first_year(conn, members, today: date, min_year: int) -> int:
    """Erstes Jahr mit möglichen Rückständen (Anfang wie in forecast)."""
    starts = [m["fee_start"] for m in members if m["fee_start"]]
    start = dues.get_check_start(conn) or (min(starts) if starts else None)
    return min(max(int(start[:4]), min_year), today.year) if start else today.year


def overview(conn, today: date, min_year: int = 1900) -> dict:
    """Alle Werte der Startseite. min_year: ältestes Jahr, das /dues anzeigen kann."""
    members = list_members(conn)
    has_tx = conn.execute('select 1 from "transaction" limit 1').fetchone() is not None
    if not members and not has_tx:
        return {"empty": True}

    overdue = []
    for year in range(_first_year(conn, members, today, min_year), today.year + 1):
        grid = dues.dues_grid(conn, year, today)
        late = [r["member_id"] for r in grid for st in r["months"] if st.status == "OVERDUE"]
        # frühere Jahre nur mit Rückstand, das laufende Jahr immer
        if late or year == today.year:
            overdue.append({"year": year, "count": len(late), "members": len(set(late))})

    # grid gehört nach der Schleife zum laufenden Jahr
    index = today.month - 1
    states = [r["months"][index].status for r in grid]
    month = {
        "month": month_of(today),
        "due": sum(s != "NONE" for s in states),
        "paid": states.count("PAID"),
        "partial": states.count("PARTIAL"),
        "open": states.count("OPEN"),
        "totals": month_totals(grid, index),
    }
    return {
        "empty": False,
        "unmatched": len(matching.unmatched(conn)),
        "proposals": dues.proposed_tx_count(conn),
        "auto": dues.auto_count(conn),
        "overdue": overdue,
        "month": month,
        "year": year_summary(conn, today.year),
    }
