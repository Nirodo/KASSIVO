"""Leistung von Migration 4: 20 000 erfundene Umsätze. Nur in tmp_path."""
import random
import time
from datetime import date

from kassivo import db, ledger


def test_migrate_20000_tx_fast(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_today", lambda: date(2026, 11, 28))
    path = tmp_path / "gross.db"
    c = db.connect(path)
    rnd = random.Random(4)
    members = [c.execute("insert into member(name) values(?)", (f"Mitglied {i}",)).lastrowid
               for i in range(300)]
    cats = [r[0] for r in c.execute("select id from category")]
    cats += [c.execute("insert into category(name, kind) values(?,?)", (n, k)).lastrowid
             for n, k in (("Miete", "out"), ("Kurse", "both"))]
    for i, iban in enumerate(("DE00000000000000000000", "DE11111111111111111111")):
        c.execute("insert into account(iban) values(?)", (iban,))
    rows = []
    for i in range(20000):
        amount = rnd.choice([1, 1, 1, -1]) * rnd.randint(0, 20000)
        day = f"{rnd.randint(2019, 2026)}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
        cat = rnd.choice(cats + [None, None])
        member = rnd.choice(members) if rnd.random() < 0.5 else None
        rows.append((1 + i % 2, day, amount, f"k{i}", cat, member))
    c.executemany('insert into "transaction"(account_id, booking_date, amount_cents,'
                  " dedupe_key, category_id, member_id) values(?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    start = time.perf_counter()
    c = db.connect(path, migrations={**db.MIGRATIONS, 4: db._migrate_to_4},
                   backup_dir=tmp_path / "backups")
    elapsed = time.perf_counter() - start
    try:
        assert db.schema_version(c) == 4
        assert elapsed < 10, f"Migration dauerte {elapsed:.1f} s"
        assert ledger.check(c) == []
        assert c.execute("select count(*) from posting").fetchone()[0] > 10000
    finally:
        c.close()
