"""Jahresauswertung nach Gegenkonten aus dem Journal (Schema 4). Nur erfundene Daten."""
from datetime import date

import pytest

from kassivo import db, ledger, reports

_n = [0]


def _acc(c, number):
    return c.execute("select id from ledger_account where number = ?", (number,)).fetchone()[0]


def _tx(c, day, amount, account=None):
    _n[0] += 1
    c.execute("insert or ignore into account(id, iban) values(1, 'DE00TEST')")
    t = c.execute('insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key)'
                  " values(1,?,?,?)", (day, amount, f"r{_n[0]}")).lastrowid
    if account is not None:
        ledger.set_counter_account(c, t, account, source="manual")
    return t


def _groups(c, year=2026):
    return {g["name"]: (g["income_cents"], g["expense_cents"])
            for g in reports._by_category_4(c, f"{year:04d}")}


def test_by_category_after_accounts(conn4):
    _tx(conn4, "2026-03-05", 1700, _acc(conn4, 3000))
    _tx(conn4, "2026-04-05", -5000, _acc(conn4, 4000))
    _tx(conn4, "2025-04-05", -999, _acc(conn4, 4000))
    assert reports._by_category_4(conn4, "2026") == [
        {"name": "Hauptverein", "income_cents": 0, "expense_cents": 5000},
        {"name": "Beiträge", "income_cents": 1700, "expense_cents": 0},
    ]


def test_unbooked_is_ohne_kategorie(conn4):
    _tx(conn4, "2026-03-05", 700)
    _tx(conn4, "2026-03-06", -300)
    _tx(conn4, "2026-03-07", 0)
    assert _groups(conn4) == {"Ohne Kategorie": (700, 300)}


def test_outgoing_on_income_account_is_expense_of_group(conn4):
    beitraege = _acc(conn4, 3000)
    _tx(conn4, "2026-03-05", 1700, beitraege)
    _tx(conn4, "2026-03-06", -300, beitraege)  # Rückbuchung mindert den Ertrag
    assert _groups(conn4) == {"Beiträge": (1700, 300)}


def test_split_groups_by_sign(conn4):
    t = _tx(conn4, "2026-03-05", 1000)
    ledger.book_tx(conn4, t, [ledger.Part(_acc(conn4, 3000), 1200),
                              ledger.Part(_acc(conn4, 4100), -200)], source="manual")
    assert _groups(conn4) == {"Beiträge": (1200, 0), "Bankgebühren": (0, 200)}


def test_reversed_booking_counts_once(conn4):
    t = _tx(conn4, "2026-03-05", 1700, _acc(conn4, 3100))
    ledger.set_counter_account(conn4, t, _acc(conn4, 3000), source="manual")
    ledger.set_counter_account(conn4, t, None, source="manual")
    ledger.set_counter_account(conn4, t, _acc(conn4, 3200), source="manual")
    assert _groups(conn4) == {"Zinsen": (1700, 0)}


def test_group_sums_match_transactions(conn4):
    _tx(conn4, "2026-01-01", 1700, _acc(conn4, 3000))
    _tx(conn4, "2026-12-31", -400, _acc(conn4, 4000))
    _tx(conn4, "2026-06-01", 250)
    g = _groups(conn4)
    assert sum(i for i, _ in g.values()) == 1950
    assert sum(e for _, e in g.values()) == 400


def test_second_bank_account_counts(conn4):
    conn4.execute("insert into account(id, iban) values(2, 'DE11111111111111111111')")
    t = conn4.execute('insert into "transaction"(account_id, booking_date, amount_cents,'
                      " dedupe_key) values(2, '2026-02-01', 500, 'b2')").lastrowid
    ledger.set_counter_account(conn4, t, _acc(conn4, 3100), source="manual")
    assert _groups(conn4) == {"Spenden": (500, 0)}


def test_report_equal_before_and_after_migration(tmp_path, monkeypatch):
    """Gleiche Daten über Schema 3 (Kategorien) und Schema 4 (Journal)."""
    monkeypatch.setattr(db, "_today", lambda: date(2026, 11, 28))
    path = tmp_path / "k.db"
    c = db.connect(path, migrations={k: db.MIGRATIONS[k] for k in (2, 3)})
    c.execute("insert into account(id, iban) values(1, 'DE00TEST')")
    cats = {}
    for name, kind in (("Beiträge", None), ("Hauptverein", None), ("Sonstiges", None),
                       ("Miete", "out"), ("Feier", "both")):
        row = c.execute("select id from category where name = ?", (name,)).fetchone()
        cats[name] = row[0] if row else c.execute(
            "insert into category(name, kind) values(?,?)", (name, kind)).lastrowid
    m = c.execute("insert into member(name) values('Erika Muster')").lastrowid
    data = [("2026-01-02", 1700, "Beiträge", None), ("2026-01-03", -300, "Beiträge", None),
            ("2026-02-01", -5000, "Hauptverein", None), ("2026-02-02", 800, "Sonstiges", None),
            ("2026-02-03", -120, "Sonstiges", None), ("2026-03-01", -900, "Miete", None),
            ("2026-03-02", 400, "Feier", None), ("2026-03-03", -150, "Feier", None),
            ("2026-04-01", 600, None, None), ("2026-04-02", -70, None, None),
            ("2026-05-01", 1700, None, m), ("2026-05-02", 0, "Sonstiges", None),
            ("2025-05-01", 999, "Sonstiges", None)]
    for i, (day, amount, cat, member) in enumerate(data):
        c.execute('insert into "transaction"(account_id, booking_date, amount_cents,'
                  " category_id, member_id, dedupe_key) values(1,?,?,?,?,?)",
                  (day, amount, cats.get(cat), member, f"k{i}"))
    c.commit()
    before = {g["name"]: (g["income_cents"], g["expense_cents"])
              for g in reports.year_summary(c, 2026)["by_category"]}
    c.close()
    c = db.connect(path, migrations={**db.MIGRATIONS, 4: db._migrate_to_4},
                   backup_dir=tmp_path / "backups")
    after = _groups(c)
    c.close()
    # Mitglied ohne Kategorie zählt laut Spec jetzt bei "Beiträge"
    before["Beiträge"] = (before["Beiträge"][0] + 1700, before["Beiträge"][1])
    before["Ohne Kategorie"] = (600, 70)
    assert after.pop("Sonstige Einnahmen") == (800, 0)
    assert after.pop("Sonstige Ausgaben") == (0, 120)
    assert before.pop("Sonstiges") == (800, 120)
    assert after.pop("Feier (Einnahmen)") == (400, 0)
    assert after.pop("Feier (Ausgaben)") == (0, 150)
    assert before.pop("Feier") == (400, 150)
    assert after == before


@pytest.mark.parametrize("year", [2024, 2027])
def test_empty_year(conn4, year):
    _tx(conn4, "2026-03-05", 1700, _acc(conn4, 3000))
    assert reports._by_category_4(conn4, f"{year:04d}") == []
