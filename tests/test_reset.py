"""Datenbank löschen und neu anfangen (#65). Nur erfundene Daten, nur tmp_path."""
import sqlite3
import sys
from pathlib import Path

import pytest

from kassivo import db as dbmod
from kassivo import restore
from kassivo.db import connect, get_setting, schema_version, set_setting
from kassivo.restore import RestoreError


def _fill(path):
    c = connect(path)
    c.execute("insert into member(name, iban) values('Erika Muster', 'XX00FAKE0000000001')")
    c.execute("insert into account(iban) values('XX00FAKE')")
    c.execute('insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key) '
              "values(1, '2026-03-01', 1700, 'k1')")
    c.execute("insert into membership_type(name) values('Ehrenmitglied')")
    c.commit()
    set_setting(c, "update_check", "1")
    c.close()


def _count(path, table):
    c = sqlite3.connect(path)
    try:
        return c.execute(f'select count(*) from "{table}"').fetchone()[0]
    finally:
        c.close()


@pytest.fixture
def target(tmp_path):
    p = tmp_path / "data" / "kassivo.db"
    _fill(p)
    return p


def _dirs(target):
    return target.parent / "restore", target.parent / "backups"


def _backups(target):
    d = target.parent / "backups"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def _fresh_reference(tmp_path):
    p = tmp_path / "ref" / "kassivo.db"
    connect(p).close()
    return p


def test_reset_creates_fresh_db_and_safety_copy(tmp_path, target):
    staging, backups = _dirs(target)
    safety = restore.reset(target, staging, backups)
    assert safety in _backups(target)
    assert _count(backups / safety, "member") == 1  # alter Stand steckt in der Kopie
    assert _count(target, "member") == 0
    assert _count(target, "transaction") == 0
    c = connect(target)
    try:
        assert schema_version(c) == dbmod.SCHEMA_VERSION
        assert get_setting(c, "update_check") is None  # Standard: aus
        assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
        # wie beim ersten Start: Standard-Mitgliedsarten, Startplan und Periode
        ref = connect(_fresh_reference(tmp_path))
        try:
            for table in ("membership_type", "fee_rate", "account_group", "ledger_account",
                          "period"):
                q = f'select * from "{table}" order by 1, 2'
                assert [tuple(r) for r in c.execute(q)] == [tuple(r) for r in ref.execute(q)]
        finally:
            ref.close()
    finally:
        c.close()
    assert not any(Path(str(target) + s).exists() for s in restore.SIDECARS)


def test_reset_clears_staging_but_not_updates(tmp_path, target):
    staging, backups = _dirs(target)
    staging.mkdir()
    old = staging / ("a" * 32 + ".db")
    old.write_bytes(b"alte Vorschau")
    foreign = staging / "fremd.txt"
    foreign.write_text("bleibt")
    updates = target.parent / "updates"
    updates.mkdir()
    (updates / "kassivo-9.9.9.zip").write_bytes(b"paket")
    restore.reset(target, staging, backups)
    assert not old.exists()
    assert foreign.exists()  # nur eigene Namen werden gelöscht
    assert [p.name for p in staging.iterdir() if p.name != "fremd.txt"] == []
    assert (updates / "kassivo-9.9.9.zip").read_bytes() == b"paket"


def test_reset_safety_copy_failure_deletes_nothing(target, monkeypatch):
    staging, backups = _dirs(target)
    before = target.read_bytes()

    def boom(*a, **k):
        raise OSError("platte voll")
    monkeypatch.setattr(restore.backup, "backup_db", boom)
    with pytest.raises(RestoreError) as e:
        restore.reset(target, staging, backups)
    assert str(e.value) == restore.E_SAFETY
    assert target.read_bytes() == before
    assert not staging.exists() or list(staging.iterdir()) == []


def test_reset_swap_failure_keeps_old_db_and_names_safety(target, monkeypatch):
    staging, backups = _dirs(target)
    before = target.read_bytes()

    def boom(a, b):
        raise PermissionError("gesperrt")
    monkeypatch.setattr(restore, "_swap", boom)
    with pytest.raises(RestoreError) as e:
        restore.reset(target, staging, backups)
    assert target.read_bytes() == before
    (name,) = _backups(target)
    assert name in str(e.value)
    assert list(staging.iterdir()) == []  # keine halbe neue Datenbank liegen lassen


def test_reset_fresh_db_failure_deletes_nothing(target, monkeypatch):
    staging, backups = _dirs(target)
    before = target.read_bytes()

    def bad(conn):
        raise sqlite3.OperationalError("kaputt")
    monkeypatch.setitem(dbmod.MIGRATIONS, 2, bad)
    with pytest.raises((RestoreError, dbmod.DatabaseOpenError)):
        restore.reset(target, staging, backups)
    assert target.read_bytes() == before
    assert _backups(target) == []  # gescheitert, bevor die Kopie nötig war
    assert list(staging.iterdir()) == []


def test_reset_refuses_with_open_transaction(target):
    staging, backups = _dirs(target)
    before = target.read_bytes()
    holder = sqlite3.connect(target)
    holder.execute("begin immediate")
    holder.execute("insert into member(name) values('Offen')")
    try:
        with pytest.raises(RestoreError) as e:
            restore.reset(target, staging, backups, timeout=0.1)
        assert str(e.value) == restore.E_BUSY
        assert _backups(target) == []
    finally:
        holder.rollback()
        holder.close()
    assert target.read_bytes() == before


@pytest.mark.skipif(sys.platform != "win32", reason="Dateisperre nur unter Windows")
def test_reset_with_open_connection_on_windows(target):
    staging, backups = _dirs(target)
    holder = sqlite3.connect(target)
    holder.execute("select 1").fetchall()
    try:
        with pytest.raises(RestoreError):
            restore.reset(target, staging, backups)
    finally:
        holder.close()
    assert _count(target, "member") == 1


# --- Web ---

import re  # noqa: E402

from tests.helpers import make_client  # noqa: E402

WORD = "LÖSCHEN"


@pytest.fixture
def web(target):
    return target, make_client(target)


def _form_token(html):
    m = re.search(r'name="token" value="([0-9a-f]{32})"', html)
    assert m, "Token fehlt im Formular"
    return m[1]


def _post(client, confirm=WORD, token=None, **kw):
    if token is None:
        token = _form_token(client.get("/settings/reset").text)
    return client.post("/settings/reset", data={"confirm": confirm, "token": token}, **kw)


def test_page_shows_what_is_lost(web):
    target, client = web
    client.post("/backup")
    (name,) = _backups(target)
    d = re.fullmatch(r"kassivo-(\d{8})-(\d{6})\.db", name)[1]
    r = client.get("/settings/reset")
    assert r.status_code == 200
    t = r.text
    assert "Datenbank löschen und neu anfangen" in t
    assert re.search(r"Mitglieder</th>\s*<td[^>]*>1<", t)
    assert re.search(r"Buchungen</th>\s*<td[^>]*>1<", t)
    assert f"{d[6:]}.{d[4:6]}.{d[:4]}" in t  # Datum der letzten Sicherung
    assert "backups" in t and "IBAN" in t  # Sicherungen bleiben und enthalten Daten
    assert 'action="/settings/reset"' in t and 'name="confirm"' in t
    assert "Erika Muster" not in t


def test_page_without_backup_says_none(web):
    _, client = web
    assert "noch keine" in client.get("/settings/reset").text.lower()


def test_sidebar_has_reset_entry(web):
    _, client = web
    t = client.get("/settings/reset").text
    assert re.search(r'<a href="/settings/reset" aria-current="page">', t)
    assert '<a href="/settings/reset"' in client.get("/backup").text


@pytest.mark.parametrize("confirm", ["", "löschen", "LOESCHEN", "JA", "LÖSCHEN!"])
def test_wrong_confirm_changes_nothing(web, confirm):
    target, client = web
    before = target.read_bytes()
    r = _post(client, confirm=confirm)
    assert r.status_code == 422
    assert "LÖSCHEN" in r.text
    assert target.read_bytes() == before
    assert _backups(target) == []


@pytest.mark.parametrize("confirm", [" LÖSCHEN ", "LO\u0308SCHEN"])
def test_confirm_tolerates_spaces_and_decomposed_umlaut(web, confirm):
    target, client = web
    assert _post(client, confirm=confirm).status_code == 303
    assert _count(target, "member") == 0


@pytest.mark.parametrize("token", ["", "0" * 31, "../x", "Z" * 32])
def test_bad_token_changes_nothing(web, token):
    target, client = web
    before = target.read_bytes()
    r = client.post("/settings/reset", data={"confirm": WORD, "token": token})
    assert r.status_code == 422
    assert target.read_bytes() == before


def test_reset_roundtrip_names_safety_copy(web):
    target, client = web
    r = _post(client)
    assert r.status_code == 303
    (name,) = _backups(target)
    assert r.headers["location"] == f"/settings/reset?done=1&safety={name}"
    t = client.get(r.headers["location"]).text
    assert name in t and "/backup" in t and "Wiederherstellen" in t
    assert _count(target, "member") == 0
    assert _count(target.parent / "backups" / name, "member") == 1
    # nach dem Neuanfang wieder normal nutzbar
    assert client.get("/members").status_code == 200
    assert client.get("/settings/update").status_code == 200


def test_double_submit_keeps_safety_name_and_resets_once(web):
    target, client = web
    token = _form_token(client.get("/settings/reset").text)
    first = _post(client, token=token)
    after = target.read_bytes()
    second = _post(client, token=token)
    assert second.status_code == 303
    assert second.headers["location"] == first.headers["location"]
    assert len(_backups(target)) == 1  # keine Kopie der schon leeren Datenbank
    assert target.read_bytes() == after


def test_new_token_after_reset_resets_again(web):
    target, client = web
    _post(client)
    _post(client)
    assert len(_backups(target)) == 2


def test_safety_failure_shows_error_and_deletes_nothing(web, monkeypatch):
    target, client = web
    before = target.read_bytes()

    def boom(*a, **k):
        raise OSError("platte voll")
    monkeypatch.setattr(restore.backup, "backup_db", boom)
    r = _post(client)
    assert r.status_code == 422 and restore.E_SAFETY in r.text
    assert target.read_bytes() == before


@pytest.mark.parametrize("q", ["safety=<script>x</script>", "safety=kassivo-20260101-000000.db",
                               "safety=../kassivo.db"])
def test_safety_param_validated(web, q):
    _, client = web
    t = client.get(f"/settings/reset?done=1&{q}").text
    assert "<script>x" not in t and "../kassivo.db" not in t
    assert "kassivo-20260101-000000.db" not in t


@pytest.mark.parametrize("headers", [{"Origin": "http://evil.example"},
                                     {"Sec-Fetch-Site": "cross-site"}])
def test_foreign_origin_blocked(web, headers):
    target, client = web
    token = _form_token(client.get("/settings/reset").text)
    before = target.read_bytes()
    r = client.post("/settings/reset", data={"confirm": WORD, "token": token}, headers=headers)
    assert r.status_code == 403
    assert target.read_bytes() == before


def test_memory_db_422(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = make_client(":memory:")
    r = c.post("/settings/reset", data={"confirm": WORD, "token": "0" * 32})
    assert r.status_code == 422
    assert list(tmp_path.iterdir()) == []


# --- Doku ---

ROOT = Path(__file__).resolve().parent.parent


def test_docs_mention_reset():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "/settings/reset" in readme and "LÖSCHEN" in readme
    limits = (ROOT / "src/kassivo/web/templates/wiki/grenzen.html").read_text(encoding="utf-8")
    assert "nicht einzeln löschen" in limits and 'href="/settings/reset"' in limits
