"""Backup: Funktion, Route, Sicherheit. Nur erfundene Daten in tmp_path."""
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kassivo.backup import backup_db, list_backups
from kassivo.db import connect
from tests.helpers import make_app

ROOT = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"^kassivo-\d{8}-\d{6}(-\d+)?\.db$")
NOW = datetime(2026, 3, 1, 12, 30, 45)


@pytest.fixture
def src(tmp_path):
    c = connect(tmp_path / "src.db")
    c.execute("insert into member(name) values('Erika Muster')")
    c.execute("insert into account(iban) values('DE00TEST')")
    c.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key) '
        "values(1,'2026-03-01',1700,'k1')")
    c.execute("insert into allocation(tx_id, member_id, month, cents, status) "
              "values(1,1,'2026-03',1700,'confirmed')")
    c.commit()
    yield c
    c.close()


def _count(c, table):
    return c.execute(f'select count(*) from "{table}"').fetchone()[0]


def _leftovers(d):
    return [p.name for p in d.iterdir() if p.name.endswith(".tmp")]


def test_backup_copies_rows(src, tmp_path):
    dest = tmp_path / "out"
    p = backup_db(src, dest, now=NOW)
    assert p.parent == dest and p.name == "kassivo-20260301-123045.db"
    assert NAME_RE.match(p.name)
    b = sqlite3.connect(p)
    for t in ("member", "transaction", "allocation", "membership_type", "fee_rate"):
        assert _count(b, t) == _count(src, t)
    b.close()


def test_default_clock_matches_pattern(src, tmp_path):
    assert NAME_RE.match(backup_db(src, tmp_path / "o").name)


def test_same_second_gives_distinct_files(src, tmp_path):
    names = [backup_db(src, tmp_path / "o", now=NOW).name for _ in range(3)]
    assert names == ["kassivo-20260301-123045.db", "kassivo-20260301-123045-2.db",
                     "kassivo-20260301-123045-3.db"]
    assert _leftovers(tmp_path / "o") == []


def test_existing_file_never_overwritten(src, tmp_path):
    d = tmp_path / "o"
    d.mkdir()
    (d / "kassivo-20260301-123045.db").write_bytes(b"alt")
    backup_db(src, d, now=NOW)
    assert (d / "kassivo-20260301-123045.db").read_bytes() == b"alt"


def test_dest_is_file_fails_clean(src, tmp_path):
    f = tmp_path / "datei"
    f.write_text("x")
    with pytest.raises(OSError):
        backup_db(src, f)
    assert f.read_text() == "x"


class _BrokenConn:
    in_transaction = False

    def backup(self, target, **kw):
        target.execute("create table x(a)")  # Zieldatei existiert schon halb
        raise sqlite3.OperationalError("kaputt")


def test_failure_in_backup_leaves_nothing(tmp_path):
    with pytest.raises(sqlite3.Error):
        backup_db(_BrokenConn(), tmp_path / "o", now=NOW)
    assert list((tmp_path / "o").iterdir()) == []


def test_failure_in_replace_leaves_nothing(src, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("kaputt")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        backup_db(src, tmp_path / "o", now=NOW)
    assert list((tmp_path / "o").iterdir()) == []


def test_uncommitted_rows_not_in_backup(src, tmp_path):
    src.execute("insert into member(name) values('Uncommitted')")  # offen
    p = backup_db(src, tmp_path / "o", now=NOW)
    b = sqlite3.connect(p)
    assert _count(b, "member") == 1
    b.close()
    src.commit()
    p2 = backup_db(src, tmp_path / "o", now=NOW)
    b = sqlite3.connect(p2)
    assert _count(b, "member") == 2
    b.close()


def test_uncommitted_on_other_connection(src, tmp_path):
    other = sqlite3.connect(tmp_path / "src.db")
    other.execute("insert into member(name) values('Offen')")
    p = backup_db(src, tmp_path / "o", now=NOW)
    b = sqlite3.connect(p)
    assert _count(b, "member") == 1
    b.close()
    other.close()


# --- Web ---

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "data" / "t.db"


@pytest.fixture
def client(db_path):
    return TestClient(make_app(db_path), follow_redirects=False)


def _dir(db_path):
    return db_path.parent / "backups"


def test_get_empty(client):
    r = client.get("/backup")
    assert r.status_code == 200
    assert "Sicherung erstellen" in r.text
    assert 'action="/backup"' in r.text and 'method="post"' in r.text


def test_get_does_not_create(client, db_path):
    client.get("/backup")
    assert not _dir(db_path).exists()


def test_post_creates_and_shows_name_only(client, db_path):
    r = client.post("/backup")
    assert r.status_code in (200, 303)
    if r.status_code == 303:
        assert r.headers["location"] == "/backup"
        r = client.get("/backup")
    files = list(_dir(db_path).iterdir())
    assert len(files) == 1 and NAME_RE.match(files[0].name)
    assert files[0].name in r.text
    parent = str(db_path.parent)
    assert parent not in r.text
    assert parent.replace("\\", "/") not in r.text
    assert parent.replace("\\", "&#92;") not in r.text


def test_get_lists_newest_first_with_sizes(client, db_path):
    d = _dir(db_path)
    d.mkdir(parents=True)
    for n, size in (("kassivo-20260101-000000.db", 2048),
                    ("kassivo-20260301-000000.db", 3 * 1024 * 1024),
                    ("kassivo-20260301-000000-2.db", 10)):
        (d / n).write_bytes(b"x" * size)
    t = client.get("/backup").text
    a, b, c = (t.index(n) for n in ("kassivo-20260301-000000-2.db",
                                    "kassivo-20260301-000000.db",
                                    "kassivo-20260101-000000.db"))
    assert a < b < c
    assert "3,0 MB" in t and "2,0 KB" in t
    assert str(d) not in t


def test_listing_ignores_foreign_and_caps(tmp_path):
    d = tmp_path
    for n in ("notes.txt", "kassivo-x.db", ".kassivo-20260301-000000.db.tmp",
              "kassivo-20260301-000000.db.tmp"):
        (d / n).write_text("x")
    for i in range(60):
        (d / f"kassivo-20260301-{i:06d}.db").write_text("x")
    items = list_backups(d)
    assert len(items) == 50
    assert all(NAME_RE.match(i["name"]) for i in items)
    assert items[0]["name"] == "kassivo-20260301-000059.db"
    assert list_backups(d / "gibtsnicht") == []


def test_post_origin_blocked(client, db_path):
    r = client.post("/backup", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert not _dir(db_path).exists()


def test_bad_host(client, db_path):
    r = client.post("/backup", headers={"Host": "evil.example"})
    assert r.status_code == 400
    assert not _dir(db_path).exists()


def test_memory_db_422(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = TestClient(make_app(":memory:"), follow_redirects=False)
    r = c.post("/backup")
    assert r.status_code == 422
    assert "Sicherung nur bei dateibasierter Datenbank möglich." in r.text
    assert list(tmp_path.iterdir()) == []
    assert c.get("/backup").status_code == 200


def test_names_escaped(client, monkeypatch):
    import kassivo.web.backup_routes as br
    monkeypatch.setattr(br, "list_backups", lambda d: [
        {"name": "<script>x</script>", "size": "1 KB", "stamp": "<b>"}])
    t = client.get("/backup").text
    assert "<script>x</script>" not in t and "&lt;script&gt;" in t


def test_headers_and_nav(client):
    r = client.get("/backup")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert 'href="/backup"' in r.text and ">Backup<" in r.text


def test_failure_gives_500_page_without_leftovers(db_path, monkeypatch):
    def boom(conn, target):
        target.write_text("halb")
        raise sqlite3.OperationalError("kaputt")
    c = TestClient(make_app(db_path), raise_server_exceptions=False)
    monkeypatch.setattr("kassivo.backup._copy", boom)
    assert c.post("/backup").status_code == 500
    assert list(_dir(db_path).iterdir()) == []


# --- Wächter ---

def test_gitignore_protects_data():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/" in lines and "*.db" in lines


def test_readme_not_stale():
    t = (ROOT / "README.md").read_text(encoding="utf-8")
    for s in ("python -m kassivo", "127.0.0.1", "<DB-Ordner>/backups", "CAMT",
              "KASSIVO_DB", "%LOCALAPPDATA%", "OneDrive", "Vorhandene Datenbank umziehen"):
        assert s in t


# --- Fix-Runde 1: exklusive Namensreservierung ---

def test_existing_name_from_generator_untouched(src, tmp_path, monkeypatch):
    d = tmp_path / "o"
    d.mkdir()
    (d / "kassivo-20260301-123045.db").write_bytes(b"alt")
    import kassivo.backup as kb
    monkeypatch.setattr(
        kb, "_candidates",
        lambda dest, stem: iter([dest / f"{stem}.db", dest / f"{stem}-2.db"]))
    p = backup_db(src, d, now=NOW)
    assert p.name == "kassivo-20260301-123045-2.db"
    assert (d / "kassivo-20260301-123045.db").read_bytes() == b"alt"
    assert _leftovers(d) == []


def test_two_threads_same_second(tmp_path):
    import threading
    dbp = tmp_path / "s.db"
    c = connect(dbp)
    c.execute("insert into member(name) values('A')")
    c.commit()
    c.close()
    d = tmp_path / "o"
    results, errors = [], []

    def run():
        try:
            conn = connect(dbp)
            results.append(backup_db(conn, d, now=NOW))
            conn.close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=run) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    assert len({p.name for p in results}) == 2
    for p in results:
        b = sqlite3.connect(p)
        assert _count(b, "member") == 1
        b.close()
    assert _leftovers(d) == []
    assert len(list(d.iterdir())) == 2


def test_copy_failure_removes_reserved_file(src, tmp_path, monkeypatch):
    def boom(conn, target):
        raise sqlite3.OperationalError("kaputt")
    monkeypatch.setattr("kassivo.backup._copy", boom)
    with pytest.raises(sqlite3.Error):
        backup_db(src, tmp_path / "o", now=NOW)
    assert list((tmp_path / "o").iterdir()) == []


def test_listing_skips_zero_byte(tmp_path):
    (tmp_path / "kassivo-20260101-000000.db").write_bytes(b"")
    (tmp_path / "kassivo-20260101-000001.db").write_bytes(b"x")
    assert [i["name"] for i in list_backups(tmp_path)] == ["kassivo-20260101-000001.db"]


def test_readme_restore_mentions_journal():
    t = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "journal" in t.split("Wiederherstellung (von Hand)")[1]
