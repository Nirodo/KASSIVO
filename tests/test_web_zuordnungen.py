"""Seite "Gemerkte Zuordnungen" (#55). Nur erfundene Daten."""
import re
from datetime import date

import pytest

from kassivo.db import connect
from kassivo.members import create_member
from tests.helpers import make_client

FULL = "XX00FAKEPAYER00000000001"
OTHER = "XX00FAKEPAYER00000000002"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "t.db"


@pytest.fixture
def client(db_path):
    return make_client(db_path)


@pytest.fixture
def db(db_path, client):
    c = connect(db_path)
    yield c
    c.close()


def _member(db, name):
    return create_member(db, name, "Aktiv", date(2025, 1, 1))


def _entry(db, iban, member_id):
    cur = db.execute("insert into member_iban(iban, member_id) values(?,?)", (iban, member_id))
    db.commit()
    return cur.lastrowid


def _learned(db):
    return {r[0]: r[1] for r in db.execute("select iban, member_id from member_iban")}


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _tx(db, purpose, iban=FULL, member_id=None):
    db.execute("insert or ignore into account(id, iban) values(1, 'XX00FAKEOWN')")
    cur = db.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, cp_iban,'
        " purpose, member_id, dedupe_key) values(1,'2026-01-05',1700,?,?,?,?)",
        (iban, purpose, member_id, purpose))
    db.commit()
    return cur.lastrowid


_LEARN = re.compile(r'<input type="checkbox" name="learn" value="on"( checked)?>')


@pytest.mark.parametrize("path", ["/transactions?year=all", "/unmatched"])
def test_learn_preselected_everywhere(client, db, path):
    _tx(db, "Beitrag")
    _tx(db, "Beitrag 2", member_id=_member(db, "Anna"))
    boxes = _LEARN.findall(client.get(path).text)
    assert boxes and all(b == " checked" for b in boxes)


def test_page_lists_masked(client, db):
    a = _member(db, "Anna Muster")
    _entry(db, FULL, a)
    _entry(db, OTHER, None)
    r = client.get("/zuordnungen")
    assert r.status_code == 200
    assert FULL not in r.text and OTHER not in r.text
    assert FULL[:4] in r.text and FULL[-4:] in r.text
    t = _text(r.text)
    assert "Anna Muster" in t and "zahlt für mehrere" in t
    assert "ordnet keine Zahlungen um" in t
    assert "<script" not in r.text and "style=" not in r.text.replace("<style", "")


def test_empty_page(client):
    assert "Noch nichts gemerkt" in _text(client.get("/zuordnungen").text)


def test_save_member_does_not_move_payments(client, db):
    a, b = _member(db, "A"), _member(db, "B")
    eid = _entry(db, FULL, a)
    t = _tx(db, "Beitrag", member_id=a)
    r = client.post(f"/zuordnungen/{eid}", data={"member_id": str(b)})
    assert r.status_code == 303 and r.headers["location"].startswith("/zuordnungen?note=")
    assert _learned(db) == {FULL: b}
    assert db.execute('select member_id from "transaction" where id=?', (t,)).fetchone()[0] == a
    assert "Gespeichert" in _text(client.get(r.headers["location"]).text)


def test_save_multiple(client, db):
    a = _member(db, "A")
    eid = _entry(db, FULL, a)
    r = client.post(f"/zuordnungen/{eid}", data={"member_id": "multi"})
    assert r.status_code == 303
    assert _learned(db) == {FULL: None}


def test_fix_null_from_wrong_correction(client, db):
    a = _member(db, "A")
    eid = _entry(db, FULL, None)
    assert client.post(f"/zuordnungen/{eid}", data={"member_id": str(a)}).status_code == 303
    assert _learned(db) == {FULL: a}


@pytest.mark.parametrize("value", ["", "abc", "999999", "-1"])
def test_save_invalid_member(client, db, value):
    a = _member(db, "A")
    eid = _entry(db, FULL, a)
    r = client.post(f"/zuordnungen/{eid}", data={"member_id": value})
    assert r.status_code == 422
    assert "Mitglied" in _text(r.text) and FULL not in r.text
    assert _learned(db) == {FULL: a}


def test_forget(client, db):
    a = _member(db, "A")
    eid = _entry(db, FULL, a)
    r = client.post(f"/zuordnungen/{eid}/forget")
    assert r.status_code == 303
    assert _learned(db) == {}
    assert "Vergessen" in _text(client.get(r.headers["location"]).text)


def test_stale_entry_is_4xx_with_german_text(client, db):
    a = _member(db, "A")
    eid = _entry(db, FULL, a)
    assert client.post(f"/zuordnungen/{eid}/forget").status_code == 303
    for r in (client.post(f"/zuordnungen/{eid}/forget"),  # doppelt gesendet
              client.post(f"/zuordnungen/{eid}", data={"member_id": str(a)})):
        assert 400 <= r.status_code < 500
        assert "gibt es nicht mehr" in _text(r.text)


@pytest.mark.parametrize("bad", ["0", "-1", "99999999999999999999"])
def test_bad_ids_404(client, bad):
    assert client.post(f"/zuordnungen/{bad}/forget").status_code == 404
    assert client.post(f"/zuordnungen/{bad}", data={"member_id": "multi"}).status_code == 404


@pytest.mark.parametrize("path", ["/zuordnungen/1", "/zuordnungen/1/forget"])
def test_foreign_origin_403(client, db, path):
    eid = _entry(db, FULL, _member(db, "A"))
    assert eid == 1
    r = client.post(path, data={"member_id": "multi"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert _learned(db) == {FULL: 1}


def test_changed_note_links_page(client, db):
    a, b = _member(db, "A"), _member(db, "B")
    _entry(db, FULL, a)
    t = _tx(db, "Beitrag")
    _tx(db, "Beitrag 2")
    r = client.post(f"/transactions/{t}/member",
                    data={"member_id": str(b), "learn": "on", "next": "/unmatched"})
    assert r.headers["location"] == "/unmatched?note=changed&n=0"
    html = client.get(r.headers["location"]).text
    assert "Gemerkte Zuordnungen" in _text(html)
    assert db.execute("select count(*) from \"transaction\" where member_id is null"
                      ).fetchone()[0] == 1  # kaskadiert nicht


def test_ambiguous_note_points_to_page(client, db):
    a = _member(db, "A")
    _entry(db, FULL, None)
    t = _tx(db, "Beitrag")
    r = client.post(f"/transactions/{t}/member",
                    data={"member_id": str(a), "learn": "on", "next": "/unmatched"})
    assert "Gemerkte Zuordnungen" in _text(client.get(r.headers["location"]).text)


def test_sidebar_has_page(client):
    html = client.get("/zuordnungen").text
    assert 'href="/zuordnungen" aria-current="page"' in html
