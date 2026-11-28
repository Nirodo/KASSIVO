"""Buchungsperioden (periods.py). Nur erfundene Daten, Datenbank im Speicher."""
from datetime import date

import pytest

from kassivo import periods
from kassivo.periods import PeriodError

TODAY = date(2026, 11, 28)


def _posting(c, day):
    c.execute("insert into posting(booking_date, source, created_at)"
              " values(?, 'manual', '2026-11-28T10:00:00')", (day,))


def _names(c):
    return [(r["name"], r["start_date"], r["end_date"]) for r in periods.list_periods(c)]


def test_create_validates(ledger_conn):
    c = ledger_conn
    before = _names(c)
    with pytest.raises(PeriodError, match="Ende"):
        periods.create_period(c, "Jahr 2027", date(2027, 12, 31), date(2027, 1, 1))
    with pytest.raises(PeriodError, match="Name"):
        periods.create_period(c, "  ", date(2027, 1, 1), date(2027, 12, 31))
    with pytest.raises(PeriodError, match="Name"):
        periods.create_period(c, "x" * 61, date(2027, 1, 1), date(2027, 12, 31))
    with pytest.raises(PeriodError, match="vergeben"):
        periods.create_period(c, "jahr 2026", date(2027, 1, 1), date(2027, 12, 31))
    with pytest.raises(PeriodError, match="überschneiden"):
        periods.create_period(c, "Vereinsjahr", date(2026, 7, 1), date(2027, 6, 30))
    assert _names(c) == before


def test_vereinsjahr_july_to_june(ledger_conn):
    c = ledger_conn
    pid = periods.create_period(c, " 2027/28 ", date(2027, 7, 1), date(2028, 6, 30))
    row = periods.period_for(c, "2028-01-15")
    assert row["id"] == pid and row["name"] == "2027/28"
    assert periods.period_for(c, "2027-03-01") is None
    assert periods.is_open(c, "2028-06-30") and not periods.is_open(c, "2028-07-01")


def test_ensure_period_creates_calendar_year(ledger_conn):
    c = ledger_conn
    pid = periods.ensure_period(c, "2028-05-01")
    assert _names(c)[-1] == ("Jahr 2028", "2028-01-01", "2028-12-31")
    assert periods.ensure_period(c, "2028-12-31") == pid
    assert periods.ensure_period(c, "2026-02-02") == periods.period_for(c, "2026-06-01")["id"]


def test_ensure_period_fills_gap_only(ledger_conn):
    c = ledger_conn
    periods.delete_period(c, periods.period_for(c, "2026-01-01")["id"])
    periods.create_period(c, "2026/27", date(2026, 7, 1), date(2027, 6, 30))
    periods.ensure_period(c, "2026-03-05")
    periods.ensure_period(c, "2027-09-01")
    assert _names(c) == [("Jahr 2026", "2026-01-01", "2026-06-30"),
                         ("2026/27", "2026-07-01", "2027-06-30"),
                         ("Jahr 2027", "2027-07-01", "2027-12-31")]


def test_ensure_period_name_taken_gets_suffix(ledger_conn):
    c = ledger_conn
    periods.create_period(c, "Jahr 2028", date(2029, 1, 1), date(2029, 12, 31))
    periods.ensure_period(c, "2028-02-01")
    assert ("Jahr 2028 (2)", "2028-01-01", "2028-12-31") in _names(c)


def test_ensure_period_rejects_bad_day(ledger_conn):
    with pytest.raises(PeriodError, match="Datum"):
        periods.ensure_period(ledger_conn, "2026-13-01")


def test_get_active_falls_back_to_today_then_last(ledger_conn):
    c = ledger_conn
    assert periods.get_active(c, TODAY)["name"] == "Jahr 2026"
    pid = periods.create_period(c, "Jahr 2027", date(2027, 1, 1), date(2027, 12, 31))
    periods.set_active(c, pid)
    assert periods.get_active(c, TODAY)["id"] == pid
    periods.delete_period(c, pid)
    assert periods.get_active(c, TODAY)["name"] == "Jahr 2026"
    assert periods.get_active(c, date(2030, 1, 1))["name"] == "Jahr 2026"  # letzte
    c.execute("update settings set value='kaputt' where key='active_period'")
    assert periods.get_active(c, TODAY)["name"] == "Jahr 2026"


def test_set_active_unknown_rejected(ledger_conn):
    with pytest.raises(PeriodError):
        periods.set_active(ledger_conn, 999)


def test_delete_only_without_postings(ledger_conn):
    c = ledger_conn
    _posting(c, "2026-03-01")
    pid = periods.period_for(c, "2026-03-01")["id"]
    with pytest.raises(PeriodError, match="Buchungen"):
        periods.delete_period(c, pid)
    with pytest.raises(PeriodError):
        periods.delete_period(c, 999)


def test_update_cannot_drop_postings(ledger_conn):
    c = ledger_conn
    _posting(c, "2026-03-01")
    pid = periods.period_for(c, "2026-03-01")["id"]
    with pytest.raises(PeriodError, match="außerhalb"):
        periods.update_period(c, pid, name="Jahr 2026", start=date(2026, 4, 1),
                              end=date(2026, 12, 31))
    periods.update_period(c, pid, name="Kalenderjahr 2026", start=date(2026, 1, 1),
                          end=date(2026, 6, 30))
    assert _names(c) == [("Kalenderjahr 2026", "2026-01-01", "2026-06-30")]


def test_update_validates_name_and_overlap(ledger_conn):
    c = ledger_conn
    p27 = periods.create_period(c, "Jahr 2027", date(2027, 1, 1), date(2027, 12, 31))
    with pytest.raises(PeriodError, match="vergeben"):
        periods.update_period(c, p27, name="Jahr 2026", start=date(2027, 1, 1),
                              end=date(2027, 12, 31))
    with pytest.raises(PeriodError, match="überschneiden"):
        periods.update_period(c, p27, name="Jahr 2027", start=date(2026, 12, 1),
                              end=date(2027, 12, 31))
    periods.update_period(c, p27, name="jahr 2027", start=date(2027, 1, 1),
                          end=date(2027, 12, 31))  # eigener Name in anderer Schreibweise


def test_list_periods_counts_postings(ledger_conn):
    c = ledger_conn
    _posting(c, "2026-03-01")
    _posting(c, "2026-12-31")
    rows = periods.list_periods(c)
    assert [(r["name"], r["status"], r["postings"]) for r in rows] == [("Jahr 2026", "open", 2)]


def test_closed_period_is_not_open(ledger_conn):
    c = ledger_conn
    pid = periods.create_period(c, "Jahr 2025", date(2025, 1, 1), date(2025, 12, 31))
    c.execute("update period set status='closed', closed_at='2026-01-05T00:00:00' where id=?",
              (pid,))
    assert periods.period_for(c, "2025-05-01")["id"] == pid
    assert not periods.is_open(c, "2025-05-01")
    with pytest.raises(PeriodError, match="abgeschlossen"):
        periods.update_period(c, pid, name="Jahr 2025", start=date(2025, 1, 1),
                              end=date(2025, 6, 30))
