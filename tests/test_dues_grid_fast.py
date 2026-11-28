"""#49: dues_grid lädt Soll und Bezahltes einmal. Ergebnis wie vorher. Nur erfundene Daten."""
import random
from datetime import date

from kassivo import dues
from kassivo.dues import month_status
from kassivo.fees import set_fee
from kassivo.members import create_member, leave_member, list_members, set_member_type


def _reference_grid(conn, year: int, today: date) -> list[dict]:
    """Bisherige Umsetzung: je Mitglied und Monat eine Abfrage über month_status."""
    members = sorted(list_members(conn), key=lambda r: (r["name"].casefold(), r["id"]))
    months = [f"{year:04d}-{i:02d}" for i in range(1, 13)]
    return [{"member_id": m["id"], "name": m["name"],
             "months": [month_status(conn, m["id"], mo, today) for mo in months]}
            for m in members]


def _tx(conn, member_id, amount, day, n=[0]):
    n[0] += 1
    conn.execute("insert or ignore into account(id, iban) values(1, 'DE00TEST')")
    return conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, member_id,'
        " cp_name, purpose, dedupe_key) values(1,?,?,?,?,?,?)",
        (day, amount, member_id, "Erfunden", "Beitrag", f"g{n[0]}")).lastrowid


def _random_club(conn, rnd: random.Random) -> None:
    names = ["anna", "Bert", "carla", "Dora", "emil", "Anna", "fritz", "Gabi"]
    ids = []
    for i in range(14):
        eintritt = date(rnd.randint(2021, 2025), rnd.randint(1, 12), rnd.randint(1, 28))
        fee_start = None if rnd.random() < 0.8 else f"{rnd.randint(2021, 2026)}-{rnd.randint(1, 12):02d}"
        ids.append(create_member(conn, rnd.choice(names), rnd.choice(["Aktiv", "Passiv"]),
                                 eintritt, fee_start=fee_start))
    for m in rnd.sample(ids, 4):  # Artwechsel
        set_member_type(conn, m, rnd.choice(["Aktiv", "Passiv"]),
                        f"{rnd.randint(2023, 2026)}-{rnd.randint(1, 12):02d}")
    for m in rnd.sample(ids, 3):  # Austritt
        leave_member(conn, m, date(rnd.randint(2024, 2026), rnd.randint(1, 12), rnd.randint(1, 28)))
    set_fee(conn, "Aktiv", 2000, "2025-07")  # Beitragsänderung
    set_fee(conn, "Passiv", 1200, "2026-01")
    amounts = [1000, 1200, 1700, 2000, 3400, 4000, 500, 2300, 20400, 1]
    for _ in range(160):
        day = f"{rnd.randint(2022, 2026)}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
        _tx(conn, rnd.choice(ids), rnd.choice(amounts), day)
    conn.commit()
    dues.set_check_start_redistribute(conn, "2023-04")  # Prüfungsstart
    txs = [r[0] for r in conn.execute(
        "select distinct tx_id from allocation where status='proposed' order by tx_id")]
    for t in txs[:3]:  # von Hand bestätigte Vorschläge
        dues.confirm_allocations(conn, t, dues._stored(conn, t))
    auto = [r[0] for r in conn.execute(
        "select distinct tx_id from allocation where status='confirmed' and source='auto'"
        " order by tx_id")]
    for t in auto[:2]:  # Vorschläge des Nutzers
        dues.undo_auto(conn, t)


def test_dues_grid_matches_reference_on_random_data(conn):
    rnd = random.Random(49)
    _random_club(conn, rnd)
    assert dues.proposed_tx_count(conn) > 0
    assert conn.execute("select count(*) from allocation where status='confirmed'").fetchone()[0]
    for today in (date(2026, 11, 28), date(2025, 1, 1), date(2024, 6, 30)):
        for year in range(2021, 2028):
            assert dues.dues_grid(conn, year, today) == _reference_grid(conn, year, today), year


def test_dues_grid_without_check_start_and_empty(conn):
    assert dues.dues_grid(conn, 2026, date(2026, 1, 1)) == []
    m = create_member(conn, "Erika", "Aktiv", date(2026, 1, 15))
    assert dues.dues_grid(conn, 2026, date(2026, 3, 1)) == _reference_grid(conn, 2026, date(2026, 3, 1))
    _tx(conn, m, 1700, "2026-02-03")
    conn.commit()
    dues.redistribute_all(conn)
    assert dues.dues_grid(conn, 2026, date(2026, 3, 1)) == _reference_grid(conn, 2026, date(2026, 3, 1))


def test_dues_grid_queries_do_not_grow_with_members(conn):
    for i in range(30):
        create_member(conn, f"M{i}", "Aktiv", date(2024, 1, 15))
    calls = []
    conn.set_trace_callback(calls.append)
    dues.dues_grid(conn, 2026, date(2026, 11, 28))
    conn.set_trace_callback(None)
    assert len(calls) <= 10, len(calls)
