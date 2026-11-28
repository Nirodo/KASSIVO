"""Baut eine DEMO-Datenbank mit erfundenen Daten (nur für Screenshots).

Aufruf: python make_demo_db.py <Zielordner>   (legt <Zielordner>/demo.db an)
Alle Namen und IBANs sind erfunden. Deterministisch, druckt nur Zahlen.
"""
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from kassivo import categories, db, dues, fees, ingest, ledger, matching, members  # noqa: E402
from tests.helpers import make_camt, make_zip  # noqa: E402

ACCOUNT = "XX00FAKE0000009999"
NAMES = [
    "Mara Beispiel", "Jonas Muster", "Lena Probe", "Tom Test", "Sofia Demo",
    "Ben Exempel", "Clara Muster", "Felix Beispiel", "Nora Probe", "Paul Demo",
    "Emma Test", "Luca Exempel",  # 0-11 Aktiv
    "Anna Beispiel", "Max Muster", "Ida Probe", "Karl Demo",  # 12-15 Passiv
]
AKTIV, PASSIV = 1700, 1000


def iban(i: int) -> str:
    return f"XX00FAKE00000000{i:02d}"


def build(folder: Path) -> Path:
    path = folder / "demo.db"
    for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        p.unlink(missing_ok=True)
    conn = db.connect(path)
    fees.set_fee(conn, "Aktiv", AKTIV, "2026-01")
    fees.set_fee(conn, "Passiv", PASSIV, "2026-01")
    ids = []
    for i, name in enumerate(NAMES):
        typ = "Aktiv" if i < 12 else "Passiv"
        eintritt, fee_start = date(2024, 3, 1 + i), None
        if i == 13:  # tritt 2026 ein
            eintritt, fee_start = date(2026, 3, 15), "2026-04"
        ids.append(members.create_member(
            conn, name, typ, eintritt, iban=iban(i), fee_start=fee_start,
            street=f"Musterweg {i + 1}", zip_code="00000", city="Beispielstadt",
            email=f"{name.split()[0].lower()}@example.invalid"))
    members.leave_member(conn, ids[14], date(2026, 8, 31))
    dues.set_check_start(conn, "2026-01")

    # Konten aus dem Startplan (Nummern) und ein eigenes Aufwandskonto; Regeln buchen
    # über ledger.set_counter_account
    number = {r["number"]: r["id"] for r in ledger.list_accounts(conn)}
    miete = ledger.create_account(conn, "Raummiete", "expense", number=4500,
                                  group_id=ledger.find_group(conn, "expense", "Ausgaben"))
    categories.add_rule(conn, number[4000], "purpose", "Hauptverein")
    categories.add_rule(conn, miete, "purpose", "Raummiete")
    categories.add_rule(conn, number[3200], "purpose", "Zinsabrechnung")

    # Einträge je Monat
    by_month: dict[int, list[dict]] = {m: [] for m in range(1, 12)}
    n = 0

    def add(month, day, cents, name=None, cp=None, purpose=""):
        nonlocal n
        n += 1
        by_month[month].append({
            "amount": cents, "name": name, "cp_iban": cp, "purpose": purpose,
            "ref": f"DEMO{n:04d}", "booking_date": f"2026-{month:02d}-{day:02d}",
            "value_date": f"2026-{month:02d}-{day:02d}"})

    for i, name in enumerate(NAMES):
        fee = AKTIV if i < 12 else PASSIV
        for m in range(1, 12):
            if i == 13 and m < 4:
                continue
            if i == 14 and m > 8:
                continue
            day = 1 + (i + m) % 5
            if i == 2:
                day = 12 + m % 7  # zahlt spät im Monat
            if i == 7:
                if m == 1:
                    add(1, 2, fee * 12, name, iban(i), "Jahresbeitrag 2026 im Voraus")
                continue
            if i == 9:
                if m == 3:
                    add(3, 3, fee * 2, name, iban(i), "Beitrag Maerz und April")
                    continue
                if m == 4:
                    continue
            if i == 11 and m >= 10:
                continue  # Rückstand Oktober, November
            if i == 3 and m in (5, 6):
                add(m, day, 1000 if m == 5 else 2400, name, iban(i),
                    "Beitrag Teilzahlung" if m == 5 else "Beitrag Rest und Juni")
                continue
            add(m, day, fee, name, iban(i), f"Mitgliedsbeitrag {m:02d}/2026")
            if i == 5 and m == 7:
                add(7, 4, fee, name, iban(i), "Mitgliedsbeitrag Juli nochmal")
            if i == 1 and m == 9:
                add(9, 5, fee, name, iban(i), "Mitgliedsbeitrag September doppelt")
    add(6, 3, 1700, "Verein Unbekannt e.V.", "XX00FAKE0000000501", "Spende Sommerfest")
    add(9, 4, 1000, "Pia Fremd", "XX00FAKE0000000502", "Beitrag?")
    add(6, 30, 321, None, None, "Zinsabrechnung 2. Quartal")
    add(9, 30, 287, None, None, "Zinsabrechnung 3. Quartal")
    for m in range(1, 12):
        add(m, 6, -12000, "Gemeinde Beispielstadt", "XX00FAKE0000000601",
            f"Raummiete {m:02d}/2026")
    for m in (3, 6, 9):
        add(m, 10, -45000, "Hauptverein Beispiel e.V.", "XX00FAKE0000000602",
            f"Abfuehrung Hauptverein Quartal {m // 3}")
    add(8, 14, -8350, "Sport Muster GmbH", "XX00FAKE0000000603", "Pokale und Urkunden")

    bal, files = 1_000_000, {}
    for m in range(1, 12):
        es = sorted(by_month[m], key=lambda e: e["booking_date"])
        files[f"2026-{m:02d}.xml"] = make_camt(ACCOUNT, bal, es, f"2026-{m:02d}-28")
        bal += sum(e["amount"] for e in es)
    zpath = folder / "demo-2026.zip"
    zpath.write_bytes(make_zip(files))

    conn.commit()  # import_path verlangt: keine offene Transaktion
    res = ingest.import_path(conn, zpath)
    categories.apply_rules(conn, res.new_tx_ids)
    conn.commit()
    assigned = matching.auto_assign(conn, res.new_tx_ids)
    conn.close()
    print(f"members={len(ids)} transactions={res.new} assigned={assigned}")
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="kassivo-demo-"))
    build(target)
    c = db.connect(target / "demo.db")
    q = lambda s: c.execute(s).fetchone()[0]  # noqa: E731
    print("confirmed_tx=%d proposed_tx=%d unmatched=%d" % (
        q("select count(distinct tx_id) from allocation where status='confirmed'"),
        q("select count(distinct tx_id) from allocation where status='proposed'"),
        len(matching.unmatched(c))))
