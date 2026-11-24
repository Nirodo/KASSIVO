import sqlite3
from pathlib import Path

SCHEMA = """
create table if not exists account(
  id integer primary key, iban text not null unique, label text);
create table if not exists membership_type(
  id integer primary key, name text not null unique);
create table if not exists fee_rate(
  type_id integer not null references membership_type(id),
  cents integer not null, valid_from text not null,
  primary key(type_id, valid_from));
create table if not exists member(
  id integer primary key, name text not null, street text, zip text, city text,
  email text, phone text, iban text, eintritt text, austritt text, fee_start text);
create table if not exists member_type_history(
  member_id integer not null references member(id),
  type_id integer not null references membership_type(id),
  valid_from text not null,
  primary key(member_id, valid_from));
create table if not exists member_iban(
  iban text primary key, member_id integer not null references member(id));
create table if not exists category(
  id integer primary key, name text not null unique,
  kind text not null check(kind in ('in','out','both')));
create table if not exists category_rule(
  id integer primary key,
  category_id integer not null references category(id),
  field text not null, contains text not null, priority integer not null default 0,
  min_cents integer, max_cents integer);
create table if not exists import_batch(
  id integer primary key, filename text, imported_at text,
  new_count integer not null default 0, dup_count integer not null default 0);
create table if not exists "transaction"(
  id integer primary key,
  account_id integer not null references account(id),
  booking_date text not null, value_date text,
  amount_cents integer not null, bank_ref text,
  cp_name text, cp_iban text, purpose text,
  dedupe_key text not null,
  category_id integer references category(id),
  member_id integer references member(id),
  batch_id integer references import_batch(id),
  unique(account_id, dedupe_key));
create table if not exists allocation(
  id integer primary key,
  tx_id integer not null references "transaction"(id),
  member_id integer not null references member(id),
  month text not null, cents integer not null,
  status text not null check(status in ('proposed','confirmed')));
create table if not exists settings(key text primary key, value text);
"""


def _seed(conn: sqlite3.Connection) -> None:
    if conn.execute("select count(*) from membership_type").fetchone()[0]:
        return
    for name, cents in (("Aktiv", 1700), ("Passiv", 1000)):
        cur = conn.execute("insert into membership_type(name) values(?)", (name,))
        conn.execute(
            "insert into fee_rate(type_id, cents, valid_from) values(?,?,?)",
            (cur.lastrowid, cents, "1900-01"),
        )
    conn.executemany(
        "insert or ignore into category(name, kind) values(?,?)",
        [("Beiträge", "in"), ("Hauptverein", "out"), ("Sonstiges", "both")],
    )


def connect(path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _seed(conn)
    conn.commit()
    return conn


def get_setting(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("select value from settings where key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "insert into settings(key, value) values(?,?) "
        "on conflict(key) do update set value=excluded.value",
        (key, value),
    )
    conn.commit()
