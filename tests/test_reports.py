"""Jahresauswertung. Nur erfundene Daten."""
import pytest

from kassivo import ledger
from kassivo.reports import year_summary

_n = [0]


def _account(conn, name):
    """Konto mit diesem Namen (Startplan oder eigenes Ertragskonto). Die Gruppe im Report
    heißt wie das Gegenkonto; eine Rückbuchung auf dasselbe Konto bleibt eine Gruppe."""
    row = conn.execute("select id from ledger_account where name=? order by id",
                       (name,)).fetchone()
    return row[0] if row else ledger.create_account(conn, name, "income")


def _tx(conn, day, amount, category=None, member_id=None):
    _n[0] += 1
    conn.execute("insert or ignore into account(id, iban) values(1, 'DE00TEST')")
    cur = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents,'
        " member_id, dedupe_key) values(1,?,?,?,?)",
        (day, amount, member_id, f"r{_n[0]}"),
    )
    if category and amount != 0:
        ledger.set_counter_account(conn, cur.lastrowid, _account(conn, category),
                                   source="manual")
    conn.commit()
    return cur.lastrowid


def _member(conn, name):
    cur = conn.execute("insert into member(name) values(?)", (name,))
    conn.commit()
    return cur.lastrowid


def _alloc(conn, member_id, month, cents, status="confirmed", day="2026-01-15"):
    tx = _tx(conn, day, cents, "Beiträge", member_id)
    conn.execute(
        "insert into allocation(tx_id, member_id, month, cents, status) values(?,?,?,?,?)",
        (tx, member_id, month, cents, status),
    )
    conn.commit()


def test_totals(conn):
    _tx(conn, "2026-03-05", 1700, "Beiträge")
    _tx(conn, "2026-04-05", -5000, "Hauptverein")
    s = year_summary(conn, 2026)
    assert s["income_cents"] == 1700
    assert s["expense_cents"] == 5000
    assert s["balance_cents"] == -3300


def test_other_years_do_not_count(conn):
    _tx(conn, "2025-12-31", 1000, "Beiträge")
    _tx(conn, "2027-01-01", -2000, "Hauptverein")
    _tx(conn, "2026-01-01", 300, "Beiträge")
    _tx(conn, "2026-12-31", -400, "Hauptverein")
    s = year_summary(conn, 2026)
    assert (s["income_cents"], s["expense_cents"]) == (300, 400)
    assert s["by_month"][0]["income_cents"] == 300
    assert s["by_month"][11]["expense_cents"] == 400


def test_without_category(conn):
    _tx(conn, "2026-03-05", 700)
    s = year_summary(conn, 2026)
    assert s["by_category"] == [
        {"name": "Ohne Kategorie", "income_cents": 700, "expense_cents": 0}
    ]


def test_by_month_twelve_entries(conn):
    _tx(conn, "2026-03-05", 1700, "Beiträge")
    s = year_summary(conn, 2026)
    assert [m["month"] for m in s["by_month"]] == [f"2026-{i:02d}" for i in range(1, 13)]
    assert s["by_month"][2] == {"month": "2026-03", "income_cents": 1700, "expense_cents": 0}
    assert sum(m["income_cents"] for m in s["by_month"]) == 1700


def test_category_sums_match_totals(conn):
    _tx(conn, "2026-03-05", 1700, "Beiträge")
    _tx(conn, "2026-03-06", 250)
    _tx(conn, "2026-04-05", -5000, "Hauptverein")
    _tx(conn, "2026-04-06", -90)
    s = year_summary(conn, 2026)
    assert sum(c["income_cents"] for c in s["by_category"]) == s["income_cents"]
    assert sum(c["expense_cents"] for c in s["by_category"]) == s["expense_cents"]


def test_empty_year(conn):
    s = year_summary(conn, 2026)
    assert s["income_cents"] == s["expense_cents"] == s["balance_cents"] == 0
    assert len(s["by_month"]) == 12
    assert s["by_category"] == [] and s["by_member"] == []


def test_zero_amount_counts_nowhere(conn):
    _tx(conn, "2026-03-05", 0, "Sonstige Einnahmen")
    s = year_summary(conn, 2026)
    assert s["by_category"] == []
    assert s["income_cents"] == s["expense_cents"] == 0


def test_by_member_confirmed_and_month_in_year(conn):
    anna = _member(conn, "Anna")
    bert = _member(conn, "bert")
    _alloc(conn, anna, "2026-01", 1700)
    _alloc(conn, anna, "2025-12", 1700, day="2026-01-05")
    _alloc(conn, bert, "2026-02", 1000, status="proposed")
    _alloc(conn, bert, "2026-03", 1000)
    s = year_summary(conn, 2026)
    assert s["by_member"] == [
        {"name": "Anna", "paid_cents": 1700},
        {"name": "bert", "paid_cents": 1000},
    ]


@pytest.mark.parametrize("bad", [0, 10000, -1, True, "2026", 2026.0, None])
def test_invalid_year(conn, bad):
    with pytest.raises(ValueError):
        year_summary(conn, bad)


def test_category_sort(conn):
    _tx(conn, "2026-03-05", 100, "Zeta")
    _tx(conn, "2026-03-05", 100, "alpha")
    _tx(conn, "2026-03-05", 900, "Groß")
    s = year_summary(conn, 2026)
    assert [c["name"] for c in s["by_category"]] == ["Groß", "alpha", "Zeta"]


def test_income_and_expense_same_category_one_entry(conn):
    _tx(conn, "2026-03-05", 500, "Sonstige Einnahmen")
    _tx(conn, "2026-03-06", -200, "Sonstige Einnahmen")
    s = year_summary(conn, 2026)
    assert s["by_category"] == [
        {"name": "Sonstige Einnahmen", "income_cents": 500, "expense_cents": 200}
    ]
