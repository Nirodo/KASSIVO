"""Sicherung exportieren und einspielen (#32). Nur erfundene Daten in tmp_path."""
import hashlib
import io
import json
import re
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from kassivo import __version__, restore
from kassivo import db as dbmod
from kassivo.db import MigrationError, SchemaTooNewError, connect, schema_version
from kassivo.restore import RestoreError
from tests.helpers import make_client

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 27, 9, 15, 0)
TOKEN_RE = re.compile(r"[0-9a-f]{32}")


# --- Hilfen ---

def _fill(path, members=("Erika Muster",), booking="2026-03-01"):
    c = connect(path)
    for name in members:
        c.execute("insert into member(name, iban) values(?, 'XX00FAKE0000000001')", (name,))
    c.execute("insert or ignore into account(iban) values('XX00FAKE')")
    c.execute('insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key) '
              "values(1, ?, 1700, ?)", (booking, f"k-{booking}-{len(members)}"))
    c.commit()
    return c


def _count(path, table):
    c = sqlite3.connect(path)
    try:
        return c.execute(f'select count(*) from "{table}"').fetchone()[0]
    finally:
        c.close()


def _names(path):
    c = sqlite3.connect(path)
    try:
        return [r[0] for r in c.execute("select name from member order by id")]
    finally:
        c.close()


def _export(tmp_path, name="quelle.db", **kw):
    src = tmp_path / "quelle" / name
    c = _fill(src, **kw)
    out = tmp_path / "export.kassivo"
    restore.export_backup(c, out, now=NOW)
    c.close()
    return out


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            z.writestr(n, d)
    return buf.getvalue()


def _parts(kassivo: Path):
    with zipfile.ZipFile(kassivo) as z:
        return json.loads(z.read("manifest.json")), z.read("kassivo.db")


def _manifest_for(data: bytes, **over):
    m = {"format": "kassivo-backup", "format_version": 1, "app_version": "1.2.0",
         "schema_version": dbmod.SCHEMA_VERSION, "created_at": "2026-11-27T09:15:00",
         "db_sha256": hashlib.sha256(data).hexdigest(), "db_size": len(data),
         "members": 1, "transactions": 1}
    m.update(over)
    return json.dumps(m).encode()


def _db_bytes(tmp_path, setup):
    """Rohe SQLite-Datei, von setup(conn) befüllt."""
    p = tmp_path / "roh" / f"{len(list(tmp_path.glob('roh/*')))}.db"
    p.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(p)
    setup(c)
    c.commit()
    c.close()
    return p.read_bytes()


def _write(tmp_path, content: bytes, name="upload.kassivo"):
    p = tmp_path / name
    p.write_bytes(content)
    return p


def _staging(tmp_path):
    return tmp_path / "ziel" / "restore"


def _staged_files(tmp_path):
    d = _staging(tmp_path)
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


# --- Export ---

def test_export_contains_manifest_and_db(tmp_path):
    out = _export(tmp_path)
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["kassivo.db", "manifest.json"]
    m, data = _parts(out)
    assert m["format"] == "kassivo-backup" and m["format_version"] == 1
    assert m["app_version"] == __version__
    assert m["schema_version"] == dbmod.SCHEMA_VERSION
    assert m["created_at"] == "2026-11-27T09:15:00"
    assert m["db_sha256"] == hashlib.sha256(data).hexdigest()
    assert m["db_size"] == len(data)
    assert m["members"] == 1 and m["transactions"] == 1
    assert data.startswith(b"SQLite format 3\x00")
    assert "hostname" not in m and "path" not in json.dumps(m).lower()


def test_export_leaves_uncommitted_out(tmp_path):
    src = tmp_path / "q.db"
    c = _fill(src)
    c.execute("insert into member(name) values('Offen')")
    out = tmp_path / "e.kassivo"
    restore.export_backup(c, out, now=NOW)
    c.rollback()
    c.close()
    m, _ = _parts(out)
    assert m["members"] == 1


def test_export_name():
    assert restore.export_name(NOW) == "kassivo-backup-20261127-091500.kassivo"


# --- Einlesen und Prüfen ---

def test_stage_upload_ok(tmp_path):
    out = _export(tmp_path, members=("A", "B"))
    st = restore.stage_upload(out, _staging(tmp_path))
    assert TOKEN_RE.fullmatch(st.token)
    assert st.path == _staging(tmp_path) / f"{st.token}.db" and st.path.exists()
    assert st.summary["members"] == 2 and st.summary["transactions"] == 1
    assert st.summary["last_booking"] == "2026-03-01"
    assert st.schema == dbmod.SCHEMA_VERSION
    assert st.app_version == __version__ and st.created == "27.11.2026 09:15"


@pytest.mark.parametrize("content", [b"", b"PK\x03\x04kaputt", b"kein zip"])
def test_broken_zip_rejected(tmp_path, content):
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(_write(tmp_path, content), _staging(tmp_path))
    assert str(e.value) == restore.E_ZIP
    assert [n for n in _staged_files(tmp_path)] == []


def test_truncated_zip_rejected(tmp_path):
    data = _export(tmp_path).read_bytes()
    with pytest.raises(RestoreError):
        restore.stage_upload(_write(tmp_path, data[: len(data) // 2]), _staging(tmp_path))
    assert _staged_files(tmp_path) == []


def test_manipulated_checksum_rejected(tmp_path):
    m, data = _parts(_export(tmp_path))
    m["db_sha256"] = "0" * 64
    p = _write(tmp_path, _zip({"manifest.json": json.dumps(m), "kassivo.db": data}))
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert str(e.value) == restore.E_CHECKSUM
    assert _staged_files(tmp_path) == []


def test_manipulated_db_rejected(tmp_path):
    m, data = _parts(_export(tmp_path))
    data = data[:-1] + bytes([data[-1] ^ 1])
    p = _write(tmp_path, _zip({"manifest.json": json.dumps(m), "kassivo.db": data}))
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert str(e.value) == restore.E_CHECKSUM


def test_size_mismatch_rejected(tmp_path):
    m, data = _parts(_export(tmp_path))
    m["db_size"] += 1
    p = _write(tmp_path, _zip({"manifest.json": json.dumps(m), "kassivo.db": data}))
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert str(e.value) == restore.E_CHECKSUM


@pytest.mark.parametrize("names", [
    ["manifest.json", "../kassivo.db"],
    ["manifest.json", "kassivo.db", "../../boese.db"],
    ["manifest.json", "sub/kassivo.db"],
    ["manifest.json", "C:/kassivo.db"],
    ["manifest.json", "kassivo.db", "extra.txt"],
    ["kassivo.db"],
    ["manifest.json"],
    ["manifest.json", "manifest.json", "kassivo.db"],
    [],
])
def test_unexpected_entries_rejected(tmp_path, names):
    _, data = _parts(_export(tmp_path))
    buf = io.BytesIO()
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # doppelte Namen
        with zipfile.ZipFile(buf, "w") as z:
            for n in names:
                z.writestr(n, _manifest_for(data) if n == "manifest.json" else data)
    before = sorted(p.name for p in tmp_path.parent.iterdir())
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(_write(tmp_path, buf.getvalue()), _staging(tmp_path))
    assert str(e.value) == restore.E_ENTRIES
    assert _staged_files(tmp_path) == []
    assert sorted(p.name for p in tmp_path.parent.iterdir()) == before  # nichts daneben
    assert not (tmp_path / "boese.db").exists()


def test_encrypted_entry_rejected(tmp_path):
    _, data = _parts(_export(tmp_path))
    raw = bytearray(_zip({"manifest.json": _manifest_for(data), "kassivo.db": data}))
    # zipfile schreibt keine Verschlüsselung: Bit 0 der Flags in allen Kopfzeilen setzen
    for sig, off in ((b"PK", 6), (b"PK", 8)):
        i = raw.find(sig)
        while i >= 0:
            raw[i + off] |= 0x1
            i = raw.find(sig, i + 4)
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(_write(tmp_path, bytes(raw)), _staging(tmp_path))
    assert str(e.value) == restore.E_ZIP


def test_oversized_db_rejected(tmp_path, monkeypatch):
    out = _export(tmp_path)
    monkeypatch.setattr(restore, "MAX_DB_BYTES", 1024)
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(out, _staging(tmp_path))
    assert str(e.value) == restore.E_TOO_BIG
    assert _staged_files(tmp_path) == []


def test_zip_bomb_stops_at_limit(tmp_path, monkeypatch):
    """Gepackt winzig, entpackt groß: Abbruch an der Grenze, nicht erst am Ende."""
    data = b"\0" * (4 * 1024 * 1024)
    p = _write(tmp_path, _zip({"manifest.json": _manifest_for(data), "kassivo.db": data}))
    assert p.stat().st_size < 64 * 1024
    monkeypatch.setattr(restore, "MAX_DB_BYTES", 1024 * 1024)
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert str(e.value) == restore.E_TOO_BIG


def test_oversized_manifest_rejected(tmp_path):
    _, data = _parts(_export(tmp_path))
    big = _manifest_for(data)[:-1] + b', "x": "' + b"a" * (restore.MAX_MANIFEST_BYTES) + b'"}'
    p = _write(tmp_path, _zip({"manifest.json": big, "kassivo.db": data}))
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert str(e.value) == restore.E_MANIFEST


@pytest.mark.parametrize("manifest", [
    b"kein json", b"[]", b'"text"', b"{}",
    "ü".encode("latin-1"),
])
def test_invalid_manifest_rejected(tmp_path, manifest):
    _, data = _parts(_export(tmp_path))
    p = _write(tmp_path, _zip({"manifest.json": manifest, "kassivo.db": data}))
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert str(e.value) == restore.E_MANIFEST


@pytest.mark.parametrize("over", [
    {"format": "anderes"}, {"format_version": 2}, {"format_version": True},
    {"schema_version": "2"}, {"schema_version": True}, {"schema_version": -1},
    {"app_version": 120}, {"app_version": "1.2.0<script>"}, {"created_at": "gestern"},
    {"created_at": 5}, {"db_sha256": "xyz"}, {"db_size": -1}, {"db_size": "12"},
])
def test_manifest_fields_checked(tmp_path, over):
    _, data = _parts(_export(tmp_path))
    p = _write(tmp_path, _zip({"manifest.json": _manifest_for(data, **over), "kassivo.db": data}))
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert str(e.value) == restore.E_MANIFEST


def test_manifest_schema_must_match_db(tmp_path):
    _, data = _parts(_export(tmp_path))
    p = _write(tmp_path, _zip({"manifest.json": _manifest_for(data, schema_version=1),
                               "kassivo.db": data}))
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert str(e.value) == restore.E_MANIFEST


def test_too_new_schema_rejected(tmp_path):
    src = tmp_path / "neu.db"
    _fill(src).close()
    c = sqlite3.connect(src)
    c.execute(f"pragma user_version = {dbmod.SCHEMA_VERSION + 1}")
    c.close()
    data = src.read_bytes()
    p = _write(tmp_path, _zip({
        "manifest.json": _manifest_for(data, schema_version=dbmod.SCHEMA_VERSION + 1),
        "kassivo.db": data}))
    with pytest.raises(SchemaTooNewError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert "neueren KASSIVO-Version" in str(e.value)
    assert _staged_files(tmp_path) == []


def test_not_sqlite_rejected(tmp_path):
    data = b"hallo welt" * 100
    p = _write(tmp_path, _zip({"manifest.json": _manifest_for(data), "kassivo.db": data}))
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert str(e.value) == restore.E_NOT_DB


def test_corrupt_sqlite_rejected(tmp_path):
    src = tmp_path / "k.db"
    c = _fill(src, members=[f"M{i}" for i in range(300)])
    c.close()
    raw = bytearray(src.read_bytes())
    page = 4096 if len(raw) > 8192 else 1024
    raw[page * 2: page * 2 + 200] = b"\xff" * 200  # Kopf einer Tabellenseite zerstören
    data = bytes(raw)
    p = _write(tmp_path, _zip({"manifest.json": _manifest_for(data), "kassivo.db": data}))
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert str(e.value) in (restore.E_CORRUPT, restore.E_NOT_DB, restore.E_SCHEMA)
    assert _staged_files(tmp_path) == []


def test_foreign_sqlite_rejected(tmp_path):
    def setup(c):
        c.execute("create table fremd(x)")
        c.execute(f"pragma user_version = {dbmod.SCHEMA_VERSION}")
    data = _db_bytes(tmp_path, setup)
    p = _write(tmp_path, _zip({"manifest.json": _manifest_for(data), "kassivo.db": data}))
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert str(e.value) == restore.E_SCHEMA


@pytest.mark.parametrize("extra", [
    "create trigger t after insert on member begin delete from member; end",
    "create view v as select * from member",
])
def test_triggers_and_views_rejected(tmp_path, extra):
    src = tmp_path / "t.db"
    _fill(src).close()
    c = sqlite3.connect(src)
    c.execute(extra)
    c.commit()
    c.close()
    data = src.read_bytes()
    p = _write(tmp_path, _zip({"manifest.json": _manifest_for(data), "kassivo.db": data}))
    with pytest.raises(RestoreError) as e:
        restore.stage_upload(p, _staging(tmp_path))
    assert str(e.value) == restore.E_SCHEMA


def test_new_stage_clears_old_staging(tmp_path):
    out = _export(tmp_path)
    a = restore.stage_upload(out, _staging(tmp_path))
    (_staging(tmp_path) / "fremd.txt").write_text("bleibt")
    b = restore.stage_upload(out, _staging(tmp_path))
    assert not a.path.exists() and b.path.exists()
    assert (_staging(tmp_path) / "fremd.txt").exists()


def test_stage_local(tmp_path):
    src = tmp_path / "ziel" / "kassivo.db"
    c = _fill(src)
    from kassivo.backup import backup_db
    b = backup_db(c, tmp_path / "ziel" / "backups", now=NOW)
    c.close()
    before = b.read_bytes()
    st = restore.stage_local(b, _staging(tmp_path))
    assert st.summary["members"] == 1 and st.created == "27.11.2026 09:15:00"
    assert st.app_version is None
    assert b.read_bytes() == before  # Sicherung selbst bleibt unberührt


def test_stage_local_v0_from_1_1_1(tmp_path):
    """Sicherungen aus 1.1.1 tragen Schema 0, haben aber alle Tabellen."""
    def setup(c):
        c.executescript(dbmod.SCHEMA)
    p = tmp_path / "backups" / "kassivo-20250101-000000.db"
    p.parent.mkdir()
    p.write_bytes(_db_bytes(tmp_path, setup))
    st = restore.stage_local(p, _staging(tmp_path))
    assert st.schema == 0


# --- Einspielen ---

@pytest.fixture
def target(tmp_path):
    p = tmp_path / "ziel" / "kassivo.db"
    _fill(p, members=("Alt Eins", "Alt Zwei", "Alt Drei")).close()
    return p


def _backups(target):
    d = target.parent / "backups"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def test_restore_replaces_and_keeps_safety_copy(tmp_path, target):
    st = restore.stage_upload(_export(tmp_path), _staging(tmp_path))
    res = restore.restore(target, st.token, _staging(tmp_path), target.parent / "backups")
    assert _names(target) == ["Erika Muster"]
    assert res.safety in _backups(target)
    assert _names(target.parent / "backups" / res.safety) == ["Alt Eins", "Alt Zwei", "Alt Drei"]
    assert res.members == 1 and res.transactions == 1
    assert not st.path.exists()
    assert not Path(str(target) + "-journal").exists()
    c = connect(target)
    assert schema_version(c) == dbmod.SCHEMA_VERSION
    assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
    c.close()


def test_restore_old_schema_is_migrated_with_backup(tmp_path, target):
    def setup(c):
        c.executescript(dbmod.SCHEMA)
        c.execute("pragma user_version = 1")
        c.execute("insert into member(name) values('Vor Migration')")
        c.execute("insert into account(iban) values('XX00FAKE')")
        c.execute('insert into "transaction"(account_id, booking_date, amount_cents, dedupe_key)'
                  " values(1,'2025-01-02',1700,'k')")
        c.execute("insert into allocation(tx_id, member_id, month, cents, status)"
                  " values(1,1,'2025-01',1700,'proposed')")
    data = _db_bytes(tmp_path, setup)
    p = _write(tmp_path, _zip({"manifest.json": _manifest_for(data, schema_version=1),
                               "kassivo.db": data}))
    st = restore.stage_upload(p, _staging(tmp_path))
    assert st.schema == 1
    res = restore.restore(target, st.token, _staging(tmp_path), target.parent / "backups")
    c = connect(target)
    assert schema_version(c) == dbmod.SCHEMA_VERSION
    assert c.execute("select source from allocation").fetchone()[0] == "auto"
    c.close()
    # Sicherung vor der Umstellung (Stand Schema 1) und Sicherheitskopie des alten Stands
    assert len(_backups(target)) == 2 and res.safety in _backups(target)
    olds = [n for n in _backups(target) if n != res.safety]
    assert _names(target.parent / "backups" / olds[0]) == ["Vor Migration"]


def test_restore_unknown_token(tmp_path, target):
    before = target.read_bytes()
    for token in ("0" * 32, "../kassivo", "ABC"):
        with pytest.raises(RestoreError) as e:
            restore.restore(target, token, _staging(tmp_path), target.parent / "backups")
        assert str(e.value) == restore.E_EXPIRED
    assert target.read_bytes() == before
    assert _backups(target) == []


def test_restore_safety_copy_failure_changes_nothing(tmp_path, target, monkeypatch):
    st = restore.stage_upload(_export(tmp_path), _staging(tmp_path))
    before = target.read_bytes()

    def boom(*a, **k):
        raise OSError("platte voll")
    monkeypatch.setattr(restore.backup, "backup_db", boom)
    with pytest.raises(RestoreError) as e:
        restore.restore(target, st.token, _staging(tmp_path), target.parent / "backups")
    assert str(e.value) == restore.E_SAFETY
    assert target.read_bytes() == before


def test_restore_migration_failure_changes_nothing(tmp_path, target, monkeypatch):
    def setup(c):
        c.executescript(dbmod.SCHEMA)
        c.execute("pragma user_version = 1")
    data = _db_bytes(tmp_path, setup)
    p = _write(tmp_path, _zip({"manifest.json": _manifest_for(data, schema_version=1),
                               "kassivo.db": data}))
    st = restore.stage_upload(p, _staging(tmp_path))
    before = target.read_bytes()

    def bad(conn):
        raise sqlite3.OperationalError("kaputt")
    monkeypatch.setitem(dbmod.MIGRATIONS, 2, bad)
    with pytest.raises(MigrationError):
        restore.restore(target, st.token, _staging(tmp_path), target.parent / "backups")
    assert target.read_bytes() == before


def test_restore_replace_failure_changes_nothing(tmp_path, target, monkeypatch):
    st = restore.stage_upload(_export(tmp_path), _staging(tmp_path))
    before = target.read_bytes()

    def boom(a, b):
        raise PermissionError("gesperrt")
    monkeypatch.setattr(restore, "_swap", boom)
    with pytest.raises(RestoreError) as e:
        restore.restore(target, st.token, _staging(tmp_path), target.parent / "backups")
    assert str(e.value).startswith(restore.E_REPLACE.split("{")[0])
    assert target.read_bytes() == before
    assert len(_backups(target)) == 1  # Sicherheitskopie bleibt


def test_restore_refuses_with_leftover_journal(tmp_path, target):
    st = restore.stage_upload(_export(tmp_path), _staging(tmp_path))
    before = target.read_bytes()
    holder = sqlite3.connect(target)
    holder.execute("begin immediate")
    holder.execute("insert into member(name) values('Offen')")
    try:
        with pytest.raises(RestoreError) as e:
            restore.restore(target, st.token, _staging(tmp_path), target.parent / "backups",
                            timeout=0.1)
        assert str(e.value) == restore.E_BUSY
    finally:
        holder.rollback()
        holder.close()
    assert target.read_bytes() == before


@pytest.mark.skipif(sys.platform != "win32", reason="Dateisperre nur unter Windows")
def test_restore_with_open_connection_on_windows(tmp_path, target):
    st = restore.stage_upload(_export(tmp_path), _staging(tmp_path))
    holder = sqlite3.connect(target)
    holder.execute("select 1").fetchall()
    try:
        with pytest.raises(RestoreError):
            restore.restore(target, st.token, _staging(tmp_path), target.parent / "backups")
    finally:
        holder.close()
    assert _names(target) == ["Alt Eins", "Alt Zwei", "Alt Drei"]


def test_restore_revalidates_staged_file(tmp_path, target):
    st = restore.stage_upload(_export(tmp_path), _staging(tmp_path))
    st.path.write_bytes(b"inzwischen kaputt")
    before = target.read_bytes()
    with pytest.raises(RestoreError):
        restore.restore(target, st.token, _staging(tmp_path), target.parent / "backups")
    assert target.read_bytes() == before


# --- Web ---

@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "data" / "kassivo.db"
    _fill(p, members=("Alt Eins", "Alt Zwei")).close()
    return p


@pytest.fixture
def client(db_path):
    return make_client(db_path)


def _bdir(db_path):
    return db_path.parent / "backups"


def _sdir(db_path):
    return db_path.parent / "restore"


def _upload(client, content: bytes, **kw):
    return client.post("/backup/upload", files={"file": ("x.kassivo", content)}, **kw)


def _token(html):
    m = re.search(r'name="token" value="([0-9a-f]{32})"', html)
    assert m, "Token fehlt in der Vorschau"
    return m[1]


def test_page_offers_export_and_restore(client, db_path):
    client.post("/backup")
    t = client.get("/backup").text
    assert 'action="/backup/export"' in t and "Für anderen PC exportieren" in t
    assert 'action="/backup/upload"' in t and 'enctype="multipart/form-data"' in t
    assert "Sicherung einspielen" in t
    assert 'action="/backup/local"' in t
    assert "Klarnamen" in t and "IBAN" in t


def test_export_route(client, tmp_path):
    r = client.post("/backup/export")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert re.search(r'attachment; filename="kassivo-backup-\d{8}-\d{6}\.kassivo"', cd)
    assert r.headers["cache-control"] == "no-store"
    p = _write(tmp_path, r.content, "dl.kassivo")
    m, _ = _parts(p)
    assert m["members"] == 2


def test_web_roundtrip_other_pc(tmp_path, db_path):
    """PC A exportiert, PC B spielt ein."""
    a = make_client(db_path)
    exported = a.post("/backup/export").content
    other = tmp_path / "pc-b" / "kassivo.db"
    _fill(other, members=("B Eins",)).close()
    b = make_client(other)
    r = _upload(b, exported)
    assert r.status_code == 200
    assert "ersetzt" in r.text and "Alt Eins" not in r.text  # nur Eckdaten, keine Namen
    assert "Mitglieder" in r.text and "Buchungen" in r.text
    assert str(other.parent) not in r.text and str(db_path.parent) not in r.text
    token = _token(r.text)
    assert _names(other) == ["B Eins"]  # Vorschau ändert nichts
    r = b.post("/backup/restore", data={"token": token, "confirm": "1"})
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/backup?note=restored&n=2&m=1&safety=kassivo-")
    assert _names(other) == ["Alt Eins", "Alt Zwei"]
    page = b.get(loc).text
    assert "Sicherung eingespielt" in page and "Sicherheitskopie" in page
    safety = re.search(r"safety=(kassivo-[0-9-]+\.db)", loc)[1]
    assert safety in page
    assert _names(other.parent / "backups" / safety) == ["B Eins"]
    assert "Alt Eins" in b.get("/members").text


def test_restore_requires_confirm(client, db_path, tmp_path):
    token = _token(_upload(client, _export(tmp_path)  .read_bytes()).text)
    before = db_path.read_bytes()
    r = client.post("/backup/restore", data={"token": token})
    assert r.status_code == 422 and "bestätigen" in r.text
    assert db_path.read_bytes() == before
    assert _bdir(db_path).exists() is False or list(_bdir(db_path).iterdir()) == []


@pytest.mark.parametrize("token", ["", "x" * 32, "../../kassivo", "0" * 32, "A" * 32])
def test_restore_bad_token(client, db_path, token):
    before = db_path.read_bytes()
    r = client.post("/backup/restore", data={"token": token, "confirm": "1"})
    assert r.status_code == 422
    assert db_path.read_bytes() == before


def test_upload_broken_zip_422(client, db_path):
    r = _upload(client, b"PK\x03\x04kaputt")
    assert r.status_code == 422
    assert restore.E_ZIP in r.text and "Es wurde nichts eingespielt." in r.text
    assert not _sdir(db_path).exists() or list(_sdir(db_path).iterdir()) == []


def test_upload_wrong_type_422(client):
    r = _upload(client, b"SQLite format 3\x00" + b"\0" * 100)
    assert r.status_code == 422 and "Dateityp" in r.text


def test_upload_without_file_422(client):
    assert client.post("/backup/upload").status_code == 422


def test_upload_too_new_422(client, tmp_path):
    src = tmp_path / "neu.db"
    _fill(src).close()
    c = sqlite3.connect(src)
    c.execute(f"pragma user_version = {dbmod.SCHEMA_VERSION + 1}")
    c.close()
    data = src.read_bytes()
    z = _zip({"manifest.json": _manifest_for(data, schema_version=dbmod.SCHEMA_VERSION + 1),
              "kassivo.db": data})
    r = _upload(client, z)
    assert r.status_code == 422 and "neueren KASSIVO-Version" in r.text


def test_upload_manipulated_checksum_422(client, tmp_path):
    m, data = _parts(_export(tmp_path))
    m["db_sha256"] = "f" * 64
    r = _upload(client, _zip({"manifest.json": json.dumps(m), "kassivo.db": data}))
    assert r.status_code == 422 and restore.E_CHECKSUM in r.text


def test_upload_too_big_by_header_413(client, db_path):
    r = client.post("/backup/upload", content=b"x",
                    headers={"content-length": str(200 * 1024 * 1024),
                             "content-type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413
    assert "zu groß" in r.text


def test_upload_too_big_streamed_413(client, db_path, monkeypatch, tmp_path):
    import kassivo.web.backup_routes as br
    monkeypatch.setattr(br, "MAX_UPLOAD_BYTES", 1024)
    r = _upload(client, _export(tmp_path).read_bytes())
    assert r.status_code == 413 and "zu groß" in r.text
    assert not _sdir(db_path).exists() or list(_sdir(db_path).iterdir()) == []


@pytest.mark.parametrize("path", ["/backup/export", "/backup/upload", "/backup/local",
                                  "/backup/restore"])
@pytest.mark.parametrize("headers", [{"Origin": "http://evil.example"},
                                     {"Sec-Fetch-Site": "cross-site"},
                                     {"Sec-Fetch-Site": "same-site"}])
def test_foreign_origin_blocked(client, db_path, path, headers):
    before = db_path.read_bytes()
    r = client.post(path, headers=headers)
    assert r.status_code == 403
    assert db_path.read_bytes() == before
    assert not _sdir(db_path).exists()


def test_foreign_origin_blocks_confirm_with_valid_token(client, db_path, tmp_path):
    token = _token(_upload(client, _export(tmp_path).read_bytes()).text)
    before = db_path.read_bytes()
    r = client.post("/backup/restore", data={"token": token, "confirm": "1"},
                    headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert db_path.read_bytes() == before


def test_bad_host_blocked(client, db_path):
    assert client.post("/backup/export", headers={"Host": "evil.example"}).status_code == 400


def test_local_restore_roundtrip(client, db_path):
    name = client.post("/backup").headers["location"].split("created=")[1]
    c = connect(db_path)
    c.execute("insert into member(name) values('Später')")
    c.commit()
    c.close()
    r = client.post("/backup/local", data={"name": name})
    assert r.status_code == 200 and name in r.text
    r = client.post("/backup/restore", data={"token": _token(r.text), "confirm": "1"})
    assert r.status_code == 303
    assert _names(db_path) == ["Alt Eins", "Alt Zwei"]
    assert (_bdir(db_path) / name).exists()  # die gewählte Sicherung bleibt erhalten


@pytest.mark.parametrize("name", ["", "../kassivo.db", "kassivo.db", "kassivo-20260101-000000.db",
                                  "kassivo-20260101-000000.db/../../kassivo.db"])
def test_local_restore_bad_name(client, db_path, name):
    r = client.post("/backup/local", data={"name": name})
    assert r.status_code == 422
    assert not _sdir(db_path).exists() or list(_sdir(db_path).iterdir()) == []


def test_memory_db_422(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = make_client(":memory:")
    assert c.post("/backup/export").status_code == 422
    assert _upload(c, b"PK\x03\x04").status_code == 422
    assert c.post("/backup/restore", data={"token": "0" * 32, "confirm": "1"}).status_code == 422
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("q", ["safety=<script>x</script>", "safety=kassivo-20260101-000000.db",
                               "safety=../kassivo.db"])
def test_safety_param_validated(client, q):
    t = client.get(f"/backup?note=restored&n=1&m=1&{q}").text
    assert "<script>x" not in t and "../kassivo.db" not in t
    assert "kassivo-20260101-000000.db" not in t  # existiert nicht


def test_preview_shows_older_schema_hint(client, tmp_path):
    def setup(c):
        c.executescript(dbmod.SCHEMA)
        c.execute("pragma user_version = 1")
    data = _db_bytes(tmp_path, setup)
    r = _upload(client, _zip({"manifest.json": _manifest_for(data, schema_version=1),
                              "kassivo.db": data}))
    assert r.status_code == 200 and "älteren Datenbankformat" in r.text


def test_preview_escapes_untrusted_values(client, tmp_path):
    src = tmp_path / "x.db"
    _fill(src, booking="<b>x</b>").close()
    data = src.read_bytes()
    r = _upload(client, _zip({"manifest.json": _manifest_for(data), "kassivo.db": data}))
    assert r.status_code == 200
    assert "<b>x</b>" not in r.text


# --- Wächter und Doku ---

def test_gitignore_excludes_kassivo_files():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "*.kassivo" in lines


def test_docs_describe_restore():
    w = (ROOT / "src/kassivo/web/templates/wiki/backup.html").read_text(encoding="utf-8")
    r = (ROOT / "README.md").read_text(encoding="utf-8")
    for text in (w, r):
        for s in ("Für anderen PC exportieren", "Sicherung einspielen", ".kassivo",
                  "Sicherheitskopie", "Prüfsumme", "neueren", "IBAN"):
            assert s in text, s
    assert "keinen Knopf" not in w
    g = (ROOT / "src/kassivo/web/templates/wiki/grenzen.html").read_text(encoding="utf-8")
    assert "Kein Wiederherstellen-Knopf" not in g
