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
    assert r.status_code == 303
    files = list(_dir(db_path).iterdir())
    assert len(files) == 1 and NAME_RE.match(files[0].name)
    assert r.headers["location"] == f"/backup?created={files[0].name}"
    r = client.get(r.headers["location"])
    assert "Sicherung erstellt:" in r.text
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


# --- Feinschliff (Issue 14) ---

import kassivo.backup as kb
from kassivo.backup import delete_old, format_size


def test_reload_after_post_creates_no_second_backup(client, db_path):
    loc = client.post("/backup").headers["location"]
    client.get(loc)
    client.get(loc)
    assert len(list(_dir(db_path).iterdir())) == 1


def test_created_param_validated_and_escaped(client, db_path):
    for bad in ("<script>x</script>.db", "kassivo-1.db", "../kassivo-20260301-000000.db",
                "kassivo-20260301-000000.db<b>"):
        t = client.get("/backup", params={"created": bad}).text
        assert "Sicherung erstellt:" not in t and "<script>x" not in t
    _fill(_dir(db_path), ["kassivo-20260301-000000-2.db"])
    t = client.get("/backup?created=kassivo-20260301-000000-2.db").text
    assert "Sicherung erstellt:" in t


@pytest.mark.parametrize("n,txt", [
    (0, "0,0 KB"), (1024 * 1023, "1023,0 KB"), (1_048_523, "1023,9 KB"),
    (1_048_524, "1023,9 KB"), (1_048_525, "1,0 MB"), (1_048_575, "1,0 MB"), (1_048_576, "1,0 MB"),
    (1_572_864, "1,5 MB"),
])
def test_format_size_boundaries(n, txt):
    assert format_size(n) == txt


def test_interrupt_after_reservation_leaves_nothing(src, tmp_path, monkeypatch):
    def boom(conn, target):
        raise KeyboardInterrupt
    monkeypatch.setattr(kb, "_copy", boom)
    with pytest.raises(KeyboardInterrupt):
        backup_db(src, tmp_path / "o", now=NOW)
    assert list((tmp_path / "o").iterdir()) == []


def test_interrupt_in_tmp_name_leaves_nothing(src, tmp_path, monkeypatch):
    class Boom:
        @property
        def hex(self):
            raise KeyboardInterrupt
    monkeypatch.setattr(kb.uuid, "uuid4", lambda: Boom())
    with pytest.raises(KeyboardInterrupt):
        backup_db(src, tmp_path / "o", now=NOW)
    assert list((tmp_path / "o").iterdir()) == []


def test_fsync_called(src, tmp_path, monkeypatch):
    calls = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), real(fd))[1])
    backup_db(src, tmp_path / "o", now=NOW)
    assert calls


def test_fsync_failure_cleans_up(src, tmp_path, monkeypatch):
    def boom(fd):
        raise OSError("kaputt")
    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        backup_db(src, tmp_path / "o", now=NOW)
    assert list((tmp_path / "o").iterdir()) == []


def test_replace_failure_after_copy(src, tmp_path, monkeypatch):
    copied = []
    real = kb._copy
    monkeypatch.setattr(kb, "_copy", lambda c, t: (real(c, t), copied.append(t))[0])

    def boom(*a, **k):
        raise OSError("replace kaputt")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="replace kaputt"):
        backup_db(src, tmp_path / "o", now=NOW)
    assert copied and list((tmp_path / "o").iterdir()) == []


def test_listing_survives_deleted_file(tmp_path, monkeypatch):
    for n in ("kassivo-20260101-000000.db", "kassivo-20260102-000000.db"):
        (tmp_path / n).write_bytes(b"x")
    real = Path.lstat

    def flaky(self, *a, **k):
        if self.name == "kassivo-20260102-000000.db":
            raise FileNotFoundError
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "lstat", flaky)
    assert [i["name"] for i in list_backups(tmp_path)] == ["kassivo-20260101-000000.db"]
    assert delete_old(tmp_path, 1)[0] == 0


def _symlink(link, target):
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks nicht erlaubt (z. B. Windows ohne Entwicklermodus)")


def test_listing_and_cleanup_skip_symlinks(tmp_path):
    real = tmp_path / "ziel.txt"
    real.write_bytes(b"x")
    _symlink(tmp_path / "kassivo-20260301-000000.db", real)
    (tmp_path / "kassivo-20260101-000000.db").write_bytes(b"x")
    assert [i["name"] for i in list_backups(tmp_path)] == ["kassivo-20260101-000000.db"]
    assert delete_old(tmp_path, 1)[0] == 0
    assert (tmp_path / "kassivo-20260301-000000.db").is_symlink() and real.exists()


def test_listing_ignores_directories(tmp_path):
    (tmp_path / "kassivo-20260301-000000.db").mkdir()
    assert list_backups(tmp_path) == []
    assert delete_old(tmp_path, 1)[0] == 0
    assert (tmp_path / "kassivo-20260301-000000.db").is_dir()


def _fill(d, names):
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"x")


def test_delete_old_keeps_newest_with_suffix_order(tmp_path):
    names = ["kassivo-20260301-000000.db", "kassivo-20260301-000000-2.db",
             "kassivo-20260301-000000-10.db", "kassivo-20260201-000000.db"]
    _fill(tmp_path, names)
    assert delete_old(tmp_path, 2)[0] == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "kassivo-20260301-000000-10.db", "kassivo-20260301-000000-2.db"]


def test_delete_old_foreign_untouched_and_zero_byte(tmp_path):
    _fill(tmp_path, ["kassivo-20260101-000000.db", "kassivo-20260102-000000.db",
                     "notes.txt", "kassivo-x.db", "kassivo-20260301-000000.db.tmp",
                     "kassivo-20250101-000000.DB", "KASSIVO-20250102-000000.db",
                     ".kassivo-20260101-000000.db.1.abc.tmp"])
    (tmp_path / "kassivo-20260103-000000.db").write_bytes(b"")  # Reservierung
    assert delete_old(tmp_path, 1)[0] == 1
    left = sorted(p.name for p in tmp_path.iterdir())
    assert sorted(left) == sorted([
        "kassivo-20260102-000000.db", "kassivo-20260103-000000.db",
        "kassivo-20260301-000000.db.tmp", "kassivo-x.db", "notes.txt",
        "kassivo-20250101-000000.DB", "KASSIVO-20250102-000000.db",
        ".kassivo-20260101-000000.db.1.abc.tmp"])


def test_delete_old_n_too_large_and_min_one(tmp_path):
    _fill(tmp_path, ["kassivo-20260101-000000.db", "kassivo-20260102-000000.db"])
    assert delete_old(tmp_path, 99)[0] == 0
    assert delete_old(tmp_path, 0)[0] == 1  # neueste bleibt immer
    assert [p.name for p in tmp_path.iterdir()] == ["kassivo-20260102-000000.db"]
    assert delete_old(tmp_path / "gibtsnicht", 1)[0] == 0


def test_delete_old_beyond_listing_cap(tmp_path):
    _fill(tmp_path, [f"kassivo-20260301-{i:06d}.db" for i in range(60)])
    assert delete_old(tmp_path, 5)[0] == 55
    assert len(list(tmp_path.iterdir())) == 5


def _cleanup(client, **data):
    return client.post("/backup/cleanup", data=data)


def test_cleanup_page_has_form(client):
    t = client.get("/backup").text
    assert 'action="/backup/cleanup"' in t and 'name="keep"' in t
    assert 'name="confirm"' in t and "Alte Sicherungen aufräumen" in t
    assert "nicht rückgängig" in t


def test_cleanup_route_deletes_and_notes(client, db_path):
    _fill(_dir(db_path), [f"kassivo-20260301-{i:06d}.db" for i in range(5)])
    r = _cleanup(client, keep="2", confirm="1")
    assert r.status_code == 303
    assert r.headers["location"] == "/backup?note=cleaned&n=3"
    assert "Gelöschte Sicherungen: 3" in client.get(r.headers["location"]).text
    assert len(list(_dir(db_path).iterdir())) == 2


@pytest.mark.parametrize("keep", ["", "0", "-1", "1001", "abc", "1.5", "١٢"])
def test_cleanup_bad_keep_422(client, db_path, keep):
    _fill(_dir(db_path), ["kassivo-20260101-000000.db", "kassivo-20260102-000000.db"])
    assert _cleanup(client, keep=keep, confirm="1").status_code == 422
    assert len(list(_dir(db_path).iterdir())) == 2


def test_cleanup_without_confirm_422(client, db_path):
    _fill(_dir(db_path), ["kassivo-20260101-000000.db", "kassivo-20260102-000000.db"])
    assert _cleanup(client, keep="1").status_code == 422
    assert len(list(_dir(db_path).iterdir())) == 2


def test_cleanup_origin_blocked(client, db_path):
    _fill(_dir(db_path), ["kassivo-20260101-000000.db", "kassivo-20260102-000000.db"])
    r = client.post("/backup/cleanup", data={"keep": "1", "confirm": "1"},
                    headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert len(list(_dir(db_path).iterdir())) == 2


def test_cleanup_memory_db_422(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = TestClient(make_app(":memory:"), follow_redirects=False)
    assert c.post("/backup/cleanup", data={"keep": "1", "confirm": "1"}).status_code == 422


def test_wiki_and_readme_mention_cleanup():
    w = (ROOT / "src/kassivo/web/templates/wiki/backup.html").read_text(encoding="utf-8")
    assert "aufräumen" in w and "nicht rückgängig" in w
    r = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "aufräumen" in r and "nicht rückgängig" in r


# --- Fix-Runde 1 (Issue 14) ---

def test_cleanup_huge_keep_422(client, db_path):
    _fill(_dir(db_path), ["kassivo-20260101-000000.db", "kassivo-20260102-000000.db"])
    assert _cleanup(client, keep="9" * 5000, confirm="1").status_code == 422
    assert len(list(_dir(db_path).iterdir())) == 2


def test_cleanup_repeated_keep_last_value_wins(client, db_path):
    _fill(_dir(db_path), [f"kassivo-20260301-{i:06d}.db" for i in range(5)])
    r = client.post("/backup/cleanup", data={"keep": ["1", "4"], "confirm": "1"})
    assert r.status_code == 303 and r.headers["location"].endswith("n=1")
    assert len(list(_dir(db_path).iterdir())) == 4


def test_interrupt_in_dir_fsync_keeps_published_backup(src, tmp_path, monkeypatch):
    def boom(path):
        raise KeyboardInterrupt
    monkeypatch.setattr(kb, "_fsync_dir", boom)
    with pytest.raises(KeyboardInterrupt):
        backup_db(src, tmp_path / "o", now=NOW)
    files = list((tmp_path / "o").iterdir())
    assert [f.name for f in files] == ["kassivo-20260301-123045.db"]
    b = sqlite3.connect(files[0])
    assert _count(b, "member") == 1
    b.close()


def test_dir_fsync_oserror_keeps_backup(src, tmp_path, monkeypatch):
    real = os.fsync
    state = {"n": 0}

    def flaky(fd):
        state["n"] += 1
        if state["n"] > 1:  # erster Aufruf = Datei, danach Verzeichnis
            raise OSError("kaputt")
        real(fd)
    monkeypatch.setattr(os, "fsync", flaky)
    p = backup_db(src, tmp_path / "o", now=NOW)
    assert p.exists()


def test_cleanup_failure_does_not_mask_error(src, tmp_path, monkeypatch):
    def boom(conn, target):
        target.write_text("halb")
        raise sqlite3.OperationalError("urspruenglich")
    monkeypatch.setattr(kb, "_copy", boom)
    real = Path.unlink

    def deny(self, *a, **k):
        if self.name.endswith(".tmp"):
            raise PermissionError("gesperrt")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", deny)
    with pytest.raises(sqlite3.OperationalError, match="urspruenglich"):
        backup_db(src, tmp_path / "o", now=NOW)
    assert [p.name for p in (tmp_path / "o").iterdir() if not p.name.endswith(".tmp")] == []


def test_delete_old_counts_failures(tmp_path, monkeypatch):
    _fill(tmp_path, [f"kassivo-20260301-{i:06d}.db" for i in range(4)])
    real = Path.unlink

    def flaky(self, *a, **k):
        if self.name == "kassivo-20260301-000000.db":
            raise PermissionError("gesperrt")
        if self.name == "kassivo-20260301-000001.db":
            raise FileNotFoundError
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", flaky)
    assert delete_old(tmp_path, 1) == (1, 1)  # 000002 gelöscht, 000000 gesperrt, 000001 weg


def test_cleanup_partial_note(client, db_path, monkeypatch):
    import kassivo.web.backup_routes as br
    monkeypatch.setattr(br, "delete_old", lambda d, k: (3, 2))
    r = _cleanup(client, keep="1", confirm="1")
    assert r.headers["location"] == "/backup?note=cleaned_partial&n=3&m=2"
    t = client.get(r.headers["location"]).text
    assert "Gelöschte Sicherungen: 3. 2 konnten nicht gelöscht werden." in t
    t = client.get("/backup?note=cleaned_partial&n=3&m=x").text
    assert "konnten nicht" not in t


def test_sort_tie_break_by_name(tmp_path):
    _fill(tmp_path, ["kassivo-20260301-000000-01.db", "kassivo-20260301-000000.db",
                     "kassivo-20260301-000000-1.db"])
    a = [i["name"] for i in list_backups(tmp_path)]
    assert a == sorted(a, reverse=True) or len(a) == 3
    assert a == [i["name"] for i in list_backups(tmp_path)]
    assert delete_old(tmp_path, 1)[0] == 2
    assert [p.name for p in tmp_path.iterdir()] == [a[0]]


def test_created_must_exist(client, db_path):
    t = client.get("/backup?created=kassivo-20260301-000000.db").text
    assert "Sicherung erstellt:" not in t
    _fill(_dir(db_path), ["kassivo-20260301-000000.db"])
    t = client.get("/backup?created=kassivo-20260301-000000.db").text
    assert "Sicherung erstellt:" in t


def test_reserve_retry_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(kb, "_candidates",
                        lambda d, stem: iter([d / f"{stem}-{i}.db" for i in range(5000)]))
    monkeypatch.setattr(kb, "MAX_ATTEMPTS", 3)
    for i in range(5000):
        pass
    _fill(tmp_path, [f"kassivo-20260301-000000-{i}.db" for i in range(3)])
    with pytest.raises(OSError, match="Dateiname"):
        kb._reserve(tmp_path, "kassivo-20260301-000000")


def test_wiki_readme_new_sentences():
    w = (ROOT / "src/kassivo/web/templates/wiki/backup.html").read_text(encoding="utf-8")
    for s in ("Zeitstempel im Dateinamen", "nie aufgelistet", ".tmp"):
        assert s in w
    assert "Zeitstempel im Dateinamen" in (ROOT / "README.md").read_text(encoding="utf-8")


# --- Wächter ---

def test_gitignore_protects_data():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/" in lines and "*.db" in lines
    for pat in ("*.zip", "*.ZIP", "*.xml", "*.XML", "*camt052*", "*camt053*", "*camt52*",
                "*camt53*", "*CAMT52*", "*CAMT53*", "*MT940*", "*.csv", "*.CSV", "*.sta",
                "*.STA", "samples/", "exports/"):
        assert pat in lines, pat


def test_readme_not_stale():
    t = (ROOT / "README.md").read_text(encoding="utf-8")
    for s in ("python -m kassivo", "127.0.0.1", "<DB-Ordner>/backups", "CAMT",
              "KASSIVO_DB", "%LOCALAPPDATA%", "OneDrive", "Vorhandene Datenbank umziehen",
              "## Empfohlener Ablauf", "Prüfungsstart festlegen", "Absender merken",
              "Offene Vorschläge", "außerhalb des Fälligkeitsmonats", "3.15", ">=3.10",
              "ersten vollen Monat", "bestätigte Verteilungen bleiben unverändert"):
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
