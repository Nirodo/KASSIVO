"""Migration 4 (Kategorien werden Konten, Altdaten werden verbucht). Noch nicht in
MIGRATIONS eingetragen; die Tests reichen sie selbst ein. Nur erfundene Daten in tmp_path."""
import sqlite3
from datetime import date

import pytest

from kassivo import db, ledger, restore
from kassivo.db import MigrationError

IBAN1 = "DE00000000000000000000"
IBAN2 = "DE11111111111111111111"
NAME = "Erika Muster"
PURPOSE = "Beitrag Erika"
MIGS4 = None  # in _open gesetzt, damit monkeypatch von db._migrate_to_4 greift


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(db, "_today", lambda: date(2026, 11, 28))


def _migs():
    return {**db.MIGRATIONS, 4: db._migrate_to_4}


def _cat(c, name, kind="in"):
    row = c.execute("select id from category where name=?", (name,)).fetchone()
    if row:
        return row[0]
    return c.execute("insert into category(name, kind) values(?,?)", (name, kind)).lastrowid


def _tx(c, amount, day="2026-03-01", cat=None, member=None, iban=IBAN1, no_member=0):
    c.execute("insert or ignore into account(iban) values(?)", (iban,))
    aid = c.execute("select id from account where iban=?", (iban,)).fetchone()[0]
    n = c.execute('select count(*) from "transaction"').fetchone()[0]
    cid = _cat(c, cat) if isinstance(cat, str) else cat
    return c.execute(
        'insert into "transaction"(account_id, booking_date, value_date, amount_cents,'
        " bank_ref, cp_name, cp_iban, purpose, dedupe_key, category_id, member_id, no_member)"
        " values(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, day, day, amount, f"REF{n}", NAME, IBAN2, PURPOSE, f"k{n}", cid, member,
         no_member)).lastrowid


def _v3(tmp_path, build=None):
    """Schema-3-Datei mit erfundenen Mitgliedern, Verteilungen, member_iban."""
    path = tmp_path / "kassivo.db"
    c = db.connect(path)
    assert db.schema_version(c) == 3
    m1 = c.execute("insert into member(name, iban) values(?,?)", (NAME, IBAN2)).lastrowid
    c.execute("insert into member_iban(iban, member_id) values(?,?)", (IBAN2, m1))
    c.execute("insert into member_iban(iban, member_id) values(?,?)",
              ("DE22222222222222222222", None))
    if build is not None:
        build(c, m1)
    c.commit()
    c.close()
    return path


def _open(path, **kw):
    kw.setdefault("backup_dir", path.parent / "backups")
    return db.connect(path, migrations=_migs(), **kw)


def acc(c, number):
    return c.execute("select id from ledger_account where number=?", (number,)).fetchone()[0]


def acc_by_name(c, name, kind):
    return c.execute("select id, number, group_id from ledger_account where name=? and kind=?",
                     (name, kind)).fetchone()


def valid_rows(c, tx):
    return [tuple(r) for r in c.execute(
        "select r.debit_account_id, r.credit_account_id, r.cents from posting p"
        " join posting_row r on r.posting_id=p.id where p.tx_id=? and p.reversal_of is null"
        " and not exists (select 1 from posting x where x.reversal_of=p.id)", (tx,))]


# --- Grundzustand ------------------------------------------------------------------

def test_fresh_db_gets_start_plan_and_period(tmp_path):
    c = _open(tmp_path / "neu.db")
    assert db.schema_version(c) == 4
    assert [r[0] for r in c.execute("select number from ledger_account order by number")] == \
        [t[0] for t in db.START_PLAN_4]
    assert [r[0] for r in c.execute("select name from period")] == ["Jahr 2026"]
    assert c.execute("select count(*) from posting").fetchone()[0] == 0
    tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    assert "category" not in tables and "category_rule" not in tables
    cols = {r[1] for r in c.execute('pragma table_info("transaction")')}
    assert "category_id" not in cols and "no_member" in cols
    assert ledger.check(c) == []
    assert c.execute("pragma foreign_keys").fetchone()[0] == 1
    c.close()


def test_transaction_ddl_keeps_unique_and_foreign_keys(tmp_path):
    c = _open(tmp_path / "neu.db")
    fks = {(r[2], r[3]) for r in c.execute('pragma foreign_key_list("transaction")')}
    assert fks == {("account", "account_id"), ("member", "member_id"),
                   ("import_batch", "batch_id")}
    c.execute("insert into account(iban) values(?)", (IBAN1,))
    c.execute('insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key)'
              " values(1,'2026-01-01',1,'k')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute('insert into "transaction"(account_id, booking_date, amount_cents,'
                  " dedupe_key) values(1,'2026-01-02',2,'k')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute('update "transaction" set no_member=2')
    c.close()


# --- Kategorien werden Konten ------------------------------------------------------

def test_categories_become_accounts(tmp_path):
    def build(c, m):
        _cat(c, "Miete", "out")
        _cat(c, "Müller", "both")
    c = _open(_v3(tmp_path, build))
    by = {(r["name"], r["kind"]): (r["number"], r["role"]) for r in c.execute(
        "select name, kind, number, role from ledger_account")}
    assert by[("Beiträge", "income")] == (3000, "fees")
    assert by[("Hauptverein", "expense")] == (4000, None)
    assert by[("Sonstige Einnahmen", "income")][0] == 3900
    assert by[("Sonstige Ausgaben", "expense")][0] == 4900
    assert by[("Miete", "expense")][0] == 4500
    assert by[("Müller (Einnahmen)", "income")][0] == 3500
    assert by[("Müller (Ausgaben)", "expense")][0] == 4501
    assert ("Sonstiges", "income") not in by and ("Beiträge (2)", "income") not in by
    c.close()


def test_same_name_other_kind_no_suffix(tmp_path):
    c = _open(_v3(tmp_path, lambda c, m: _cat(c, "Spenden", "out")))
    assert acc_by_name(c, "Spenden", "expense")["number"] == 4500
    assert acc_by_name(c, "Spenden", "income")["number"] == 3100
    c.close()


def test_name_collision_gets_suffix(tmp_path):
    def build(c, m):
        _cat(c, "Miete (Ausgaben)", "out")
        _cat(c, "Miete", "both")
    c = _open(_v3(tmp_path, build))
    names = {r[0] for r in c.execute("select name from ledger_account where kind='expense'")}
    assert {"Miete (Ausgaben)", "Miete (Ausgaben) (2)"} <= names
    c.close()


def test_migrated_accounts_in_start_groups(tmp_path):
    def build(c, m):
        _cat(c, "Miete", "out")
        _cat(c, "Kurs", "both")
    c = _open(_v3(tmp_path, build))
    groups = {r[0]: r[1] for r in c.execute(
        "select a.name, g.name from ledger_account a join account_group g on g.id=a.group_id")}
    assert groups["Miete"] == "Ausgaben"
    assert groups["Kurs (Einnahmen)"] == "Ertrag" and groups["Kurs (Ausgaben)"] == "Ausgaben"
    c.close()


def test_many_categories_overflow_numbers(tmp_path):
    def build(c, m):
        for i in range(600):
            _cat(c, f"Kategorie {i:03d}", "in")
    c = _open(_v3(tmp_path, build))
    nums = [r[0] for r in c.execute("select number from ledger_account where name like"
                                    " 'Kategorie %'")]
    assert len(nums) == 600 == len(set(nums))
    assert all(n <= 99999 for n in nums) and max(nums) >= 5000
    assert not any(4000 <= n < 5000 for n in nums)
    c.close()


def test_rules_keep_order_and_split_both(tmp_path):
    def build(c, m):
        rules = [("Beiträge", "purpose", "Beitrag", 10, None, None),
                 ("Sonstiges", "cp_name", "Bäckerei", 5, 100, 5000),
                 ("Hauptverein", "cp_iban", "DE99", 10, None, None)]
        for name, field, text, prio, lo, hi in rules:
            c.execute("insert into category_rule(category_id, field, contains, priority,"
                      " min_cents, max_cents) values(?,?,?,?,?,?)",
                      (_cat(c, name), field, text, prio, lo, hi))
    c = _open(_v3(tmp_path, build))
    rows = [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in c.execute(
        "select a.number, r.field, r.contains, r.priority, r.min_cents, r.max_cents"
        " from posting_rule r join ledger_account a on a.id=r.account_id"
        " order by r.priority, r.id")]
    assert rows == [(3900, "cp_name", "Bäckerei", 5, 100, 5000),
                    (4900, "cp_name", "Bäckerei", 5, 100, 5000),
                    (3000, "purpose", "Beitrag", 10, None, None),
                    (4000, "cp_iban", "DE99", 10, None, None)]
    c.close()


def test_unrepresentable_rules_dropped_or_cleaned(tmp_path):
    """Regeln, die nie greifen konnten, entfallen; negative Untergrenze heißt keine."""
    def build(c, m):
        cat = _cat(c, "Beiträge")
        for field, text, lo, hi in (("purpose", "a", -5, None), ("purpose", "b", None, -1),
                                    ("unbekannt", "c", None, None), ("purpose", "", None, None)):
            c.execute("insert into category_rule(category_id, field, contains, priority,"
                      " min_cents, max_cents) values(?,?,?,0,?,?)", (cat, field, text, lo, hi))
    c = _open(_v3(tmp_path, build))
    rows = [tuple(r) for r in c.execute("select contains, min_cents, max_cents"
                                        " from posting_rule")]
    assert rows == [("a", None, None)]
    c.close()


# --- Verbuchen ---------------------------------------------------------------------

def test_tx_with_category_booked_by_sign(tmp_path):
    ids = {}

    def build(c, m):
        ids["in"] = _tx(c, 1000, cat="Sonstiges")
        ids["out"] = _tx(c, -400, cat="Sonstiges")
        ids["refund"] = _tx(c, -1700, cat="Beiträge")
    c = _open(_v3(tmp_path, build))
    bank = acc(c, 1200)
    assert valid_rows(c, ids["in"]) == [(bank, acc(c, 3900), 1000)]
    assert valid_rows(c, ids["out"]) == [(acc(c, 4900), bank, 400)]
    assert valid_rows(c, ids["refund"]) == [(acc(c, 3000), bank, 1700)]
    src = {r[0] for r in c.execute("select source from posting")}
    assert src == {"migration"}
    assert c.execute("select count(*) from posting where text is not null").fetchone()[0] == 0
    c.close()


def test_member_without_category_booked_on_fees(tmp_path):
    ids = {}

    def build(c, m):
        ids["tx"] = _tx(c, 1700, member=m)
    c = _open(_v3(tmp_path, build))
    assert valid_rows(c, ids["tx"]) == [(acc(c, 1200), acc(c, 3000), 1700)]
    c.close()


def test_no_category_no_member_stays_unbooked(tmp_path):
    ids = {}
    c = _open(_v3(tmp_path, lambda c, m: ids.setdefault("tx", _tx(c, 1700))))
    assert valid_rows(c, ids["tx"]) == []
    c.close()


def test_zero_amount_with_category_stays_unbooked(tmp_path):
    ids = {}

    def build(c, m):
        ids["tx"] = _tx(c, 0, cat="Beiträge", member=m)
    c = _open(_v3(tmp_path, build))
    assert valid_rows(c, ids["tx"]) == []
    assert ledger.check(c) == []
    c.close()


def _snapshot(path, with_category):
    c = sqlite3.connect(path)
    cols = [r[1] for r in c.execute('pragma table_info("transaction")')
            if r[1] != "category_id"]
    tx = c.execute(f'select {", ".join(cols)} from "transaction" order by id').fetchall()
    alloc = c.execute("select * from allocation order by id").fetchall()
    mi = c.execute("select * from member_iban order by iban").fetchall()
    members = c.execute("select * from member order by id").fetchall()
    c.close()
    return cols, tx, alloc, mi, members


def test_ids_allocations_no_member_member_iban_unchanged(tmp_path):
    def build(c, m):
        t1 = _tx(c, 1700, cat="Beiträge", member=m)
        _tx(c, 900, no_member=1)
        _tx(c, -300, cat="Hauptverein", iban=IBAN2)
        c.execute('delete from "transaction" where id=?', (_tx(c, 5),))  # Lücke in den IDs
        _tx(c, 1000, member=m)
        c.execute("insert into allocation(tx_id, member_id, month, cents, status, source)"
                  " values(?,?,?,?,?,?)", (t1, m, "2026-03", 1700, "confirmed", "manual"))
        c.execute("insert into allocation(tx_id, member_id, month, cents, status, source)"
                  " values(?,?,?,?,?,?)", (t1, m, "2026-04", 0, "proposed", "auto"))
    path = _v3(tmp_path, build)
    before = _snapshot(path, True)
    _open(path).close()
    after = _snapshot(path, False)
    assert before == after


def test_sums_per_account_and_year(tmp_path):
    def build(c, m):
        _cat(c, "Miete", "out")
        for year in (2024, 2025, 2026):
            _tx(c, 1700 + year, f"{year}-02-01", cat="Beiträge")
            _tx(c, -500, f"{year}-03-01", cat="Beiträge")
            _tx(c, 300, f"{year}-04-01", cat="Sonstiges")
            _tx(c, -200, f"{year}-05-01", cat="Sonstiges")
            _tx(c, -999, f"{year}-06-01", cat="Miete")
            _tx(c, 1000, f"{year}-07-01", member=m)
    path = _v3(tmp_path, build)
    old = sqlite3.connect(path)
    before = dict(((r[0], r[1]), r[2]) for r in old.execute(
        "select coalesce(c.name, 'Beiträge'), substr(t.booking_date,1,4), sum(t.amount_cents)"
        ' from "transaction" t left join category c on c.id=t.category_id'
        " group by 1, 2"))
    old.close()
    c = _open(path)
    names = {"Beiträge": [3000], "Sonstiges": [3900, 4900], "Miete": None}
    miete = acc_by_name(c, "Miete", "expense")["id"]
    for (cat, year), cents in before.items():
        accounts = [acc(c, n) for n in names[cat]] if names[cat] else [miete]
        marks = ",".join("?" * len(accounts))
        got = c.execute(
            "select coalesce(sum(case when r.credit_account_id in (" + marks + ") then r.cents"
            " else 0 end) - sum(case when r.debit_account_id in (" + marks + ") then r.cents"
            " else 0 end), 0) from posting p join posting_row r on r.posting_id=p.id"
            " where substr(p.booking_date,1,4)=?", (*accounts, *accounts, year)).fetchone()[0]
        assert got == cents, (cat, year)
    c.close()


def test_two_bank_accounts_booked_on_own_bank_side(tmp_path):
    ids = {}

    def build(c, m):
        ids["a"] = _tx(c, 100, cat="Beiträge", iban=IBAN1)
        ids["b"] = _tx(c, 200, cat="Beiträge", iban=IBAN2)
    c = _open(_v3(tmp_path, build))
    assert valid_rows(c, ids["a"]) == [(acc(c, 1200), acc(c, 3000), 100)]
    assert valid_rows(c, ids["b"]) == [(acc(c, 1201), acc(c, 3000), 200)]
    c.close()


def test_periods_cover_all_tx_years(tmp_path):
    def build(c, m):
        _tx(c, 100, "2021-05-01", cat="Beiträge")
        _tx(c, 100, "2026-05-01", cat="Beiträge")
    c = _open(_v3(tmp_path, build))
    assert [r[0] for r in c.execute("select name from period order by start_date")] == \
        [f"Jahr {y}" for y in range(2021, 2027)]
    c.close()


def test_tx_after_today_year_gets_period(tmp_path):
    def build(c, m):
        _tx(c, 100, "2027-01-03", cat="Beiträge")
    c = _open(_v3(tmp_path, build))
    assert [r[0] for r in c.execute("select name from period order by start_date")] == \
        ["Jahr 2026", "Jahr 2027"]
    assert ledger.check(c) == []
    c.close()


# --- Fehler und Rückweg ------------------------------------------------------------

def _broken_booking(monkeypatch):
    real = db._book_migrated_4

    def wrong(conn, tx, bank, counter, amount):
        real(conn, tx, bank, counter, amount + (1 if amount > 0 else -1))
    monkeypatch.setattr(db, "_book_migrated_4", wrong)


def test_sum_mismatch_rolls_back(tmp_path, monkeypatch):
    path = _v3(tmp_path, lambda c, m: _tx(c, 1700, cat="Beiträge", member=m))
    _broken_booking(monkeypatch)
    with pytest.raises(MigrationError) as err:
        _open(path)
    text = str(err.value)
    assert "Summenvergleich" in text and "Schema 3" in text
    for secret in (NAME, IBAN1, IBAN2, PURPOSE, "Beiträge"):
        assert secret not in text
    c = sqlite3.connect(path)
    assert c.execute("pragma user_version").fetchone()[0] == 3
    assert "category_id" in {r[1] for r in c.execute('pragma table_info("transaction")')}
    assert c.execute("select count(*) from sqlite_master where name='posting'").fetchone()[0] \
        == 0
    c.close()
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_check_failure_rolls_back(tmp_path, monkeypatch):
    path = _v3(tmp_path, lambda c, m: _tx(c, 1700, cat="Beiträge"))
    monkeypatch.setattr(ledger, "check", lambda conn: ["Buchung 1: ohne Zeilen."])
    with pytest.raises(MigrationError, match="Prüfung"):
        _open(path)
    c = sqlite3.connect(path)
    assert c.execute("pragma user_version").fetchone()[0] == 3
    c.close()


def test_messages_without_personal_data(tmp_path, monkeypatch):
    def build(c, m):
        c.execute("update category set name=? where name='Hauptverein'", (NAME,))
        _tx(c, -1700, cat=NAME)
    path = _v3(tmp_path, build)
    _broken_booking(monkeypatch)
    with pytest.raises(MigrationError) as err:
        _open(path)
    for secret in (NAME, IBAN1, IBAN2, PURPOSE, "DE00"):
        assert secret not in str(err.value)


def test_migration_from_v1_and_v2(tmp_path):
    for start in (1, 2):
        path = tmp_path / f"v{start}.db"
        c = db.connect(path, migrations={k: db.MIGRATIONS[k] for k in range(2, start + 1)})
        assert db.schema_version(c) == start
        c.execute("insert into account(iban) values(?)", (IBAN1,))
        c.execute('insert into "transaction"(account_id, booking_date, amount_cents,'
                  " dedupe_key, category_id) values(1, '2026-03-01', 1700, 'k1', ?)",
                  (_cat(c, "Beiträge"),))
        c.commit()
        c.close()
        c = _open(path)
        assert db.schema_version(c) == 4
        assert c.execute("select count(*) from posting").fetchone()[0] == 1
        c.close()


def test_migration_under_hardened_connection(tmp_path):
    path = _v3(tmp_path, lambda c, m: _tx(c, 1700, cat="Beiträge"))
    c = _open(path, on_open=restore.harden, backup_before_migration=False)
    assert db.schema_version(c) == 4 and ledger.check(c) == []
    c.close()
    assert not (tmp_path / "backups").exists()


def test_second_start_is_noop(tmp_path):
    path = _v3(tmp_path, lambda c, m: _tx(c, 1700, cat="Beiträge"))
    _open(path).close()
    c = _open(path)
    assert c.execute("select count(*) from posting").fetchone()[0] == 1
    assert c.execute("select count(*) from ledger_account").fetchone()[0] == 10
    c.close()


def test_not_registered_yet():
    assert 4 not in db.MIGRATIONS and db.SCHEMA_VERSION == 3
    assert db._migrate_to_4.fk_off is True
