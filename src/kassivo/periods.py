"""Buchungsperioden wie bei Webling (E10): Name frei, Von-Bis, offen oder abgeschlossen.

Standard ist das Kalenderjahr "Jahr JJJJ" (E15). Perioden überschneiden sich nie; die
Datenbank sichert das zusätzlich per Trigger ab. Keine Funktion ruft commit() außer
set_active (über db.set_setting, nur aus Routen). Die aktive Periode ist eine
Ansichtswahl in settings.active_period, keine Eigenschaft der Daten.
"""
import sqlite3
from datetime import date, timedelta

from kassivo import db

ACTIVE_KEY = "active_period"
MAX_NAME = 60

_COLUMNS = ("select p.id, p.name, p.start_date, p.end_date, p.status, p.closed_at,"
            " (select count(*) from posting b where b.booking_date between p.start_date"
            " and p.end_date) as postings from period p")


class PeriodError(ValueError):
    """Meldung für Nutzer (deutsch, ohne Personendaten)."""


def _day(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise PeriodError("Ungültiges Datum.") from None


def _name(conn, name: str, own_id: int | None = None) -> str:
    clean = (name or "").strip()
    if not 1 <= len(clean) <= MAX_NAME:
        raise PeriodError(f"Der Name muss 1 bis {MAX_NAME} Zeichen haben.")
    for r in conn.execute("select id, name from period").fetchall():
        if r["id"] != own_id and r["name"].casefold() == clean.casefold():
            raise PeriodError("Der Name ist schon vergeben.")
    return clean


def _bounds(conn, start: date, end: date, own_id: int | None = None) -> tuple[str, str]:
    if end < start:
        raise PeriodError("Das Ende liegt vor dem Start.")
    s, e = start.isoformat(), end.isoformat()
    if conn.execute("select 1 from period where id is not ? and ? <= end_date"
                    " and ? >= start_date", (own_id, s, e)).fetchone():
        raise PeriodError("Buchungsperioden dürfen sich nicht überschneiden.")
    return s, e


def _get(conn, period_id: int) -> sqlite3.Row:
    row = conn.execute(_COLUMNS + " where p.id = ?", (period_id,)).fetchone()
    if row is None:
        raise PeriodError("Die Periode gibt es nicht (mehr).")
    return row


def create_period(conn, name: str, start: date, end: date) -> int:
    clean = _name(conn, name)
    s, e = _bounds(conn, start, end)
    return conn.execute("insert into period(name, start_date, end_date) values(?,?,?)",
                        (clean, s, e)).lastrowid


def update_period(conn, period_id: int, *, name: str, start: date, end: date) -> None:
    old = _get(conn, period_id)
    clean = _name(conn, name, period_id)
    s, e = _bounds(conn, start, end, period_id)
    if (s, e) != (old["start_date"], old["end_date"]):
        if old["status"] == "closed":
            raise PeriodError("Eine abgeschlossene Periode lässt sich nicht ändern.")
        if conn.execute("select 1 from posting where booking_date between ? and ?"
                        " and booking_date not between ? and ?",
                        (old["start_date"], old["end_date"], s, e)).fetchone():
            raise PeriodError("Die Periode enthält Buchungen außerhalb der neuen Grenzen.")
    conn.execute("update period set name = ?, start_date = ?, end_date = ? where id = ?",
                 (clean, s, e, period_id))


def delete_period(conn, period_id: int) -> None:
    if _get(conn, period_id)["postings"]:
        raise PeriodError("Die Periode enthält Buchungen und lässt sich nicht löschen.")
    conn.execute("delete from period where id = ?", (period_id,))


def list_periods(conn) -> list[sqlite3.Row]:
    return conn.execute(_COLUMNS + " order by p.start_date").fetchall()


def period_for(conn, day: str) -> sqlite3.Row | None:
    return conn.execute(_COLUMNS + " where ? between p.start_date and p.end_date",
                        (_day(day),)).fetchone()


def is_open(conn, day: str) -> bool:
    row = period_for(conn, day)
    return row is not None and row["status"] == "open"


def ensure_period(conn, day: str) -> int:
    """Periode, die day enthält. Fehlt sie: Kalenderjahr, beschnitten auf die Lücke
    zwischen den Nachbarperioden; vergebener Name bekommt " (2)", " (3)" ..."""
    found = period_for(conn, day)
    if found is not None:
        return found["id"]
    d = date.fromisoformat(_day(day))
    start, end = date(d.year, 1, 1), date(d.year, 12, 31)
    before = conn.execute("select max(end_date) from period where end_date < ?",
                          (d.isoformat(),)).fetchone()[0]
    after = conn.execute("select min(start_date) from period where start_date > ?",
                         (d.isoformat(),)).fetchone()[0]
    if before:
        start = max(start, date.fromisoformat(before) + timedelta(days=1))
    if after:
        end = min(end, date.fromisoformat(after) - timedelta(days=1))
    taken = {r[0].casefold() for r in conn.execute("select name from period").fetchall()}
    base = f"Jahr {d.year}"
    name, n = base, 1
    while name.casefold() in taken:
        n += 1
        name = f"{base} ({n})"
    return conn.execute("insert into period(name, start_date, end_date) values(?,?,?)",
                        (name, start.isoformat(), end.isoformat())).lastrowid


def get_active(conn, today: date) -> sqlite3.Row | None:
    """Gespeicherte aktive Periode; fehlt sie, die Periode mit today, sonst die letzte."""
    value = db.get_setting(conn, ACTIVE_KEY)
    if value is not None and value.isdigit():
        row = conn.execute(_COLUMNS + " where p.id = ?", (int(value),)).fetchone()
        if row is not None:
            return row
    row = period_for(conn, today.isoformat())
    if row is not None:
        return row
    return conn.execute(_COLUMNS + " order by p.start_date desc limit 1").fetchone()


def set_active(conn, period_id: int) -> None:
    """Speichert die Ansichtswahl. Committet (db.set_setting), deshalb nur aus Routen."""
    _get(conn, period_id)
    db.set_setting(conn, ACTIVE_KEY, str(int(period_id)))
