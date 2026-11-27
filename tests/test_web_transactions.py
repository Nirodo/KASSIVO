"""Weboberfläche: Buchungen, Unklar, Verteilung, Kategorien. Nur erfundene Daten."""
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from kassivo.db import connect
from kassivo.dues import month_status, set_check_start
from kassivo.members import create_member, leave_member
from tests.helpers import make_app
from tests.helpers import IBAN, OTHER_IBAN, make_camt, make_zip

THIRD_IBAN = "DE22222222222222222222"
TODAY = date.today()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "t.db"


@pytest.fixture
def client(db_path):
    return TestClient(make_app(db_path), follow_redirects=False)


@pytest.fixture
def db(db_path, client):
    c = connect(db_path)
    yield c
    c.close()


def _upload(client, entries, day="2026-03-01"):
    data = make_zip({"a.xml": make_camt(IBAN, 10000, entries, day)})
    r = client.post("/import", files={"file": ("x.zip", data, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r


E_ERIKA = {"amount": 1700, "name": "Erika Muster", "cp_iban": OTHER_IBAN,
           "purpose": "Beitrag", "ref": "R1"}
E_KIOSK = {"amount": -500, "name": "Kiosk", "purpose": "Material", "ref": "R2"}
E_SPENDE = {"amount": 2500, "name": "Hans Spender", "cp_iban": THIRD_IBAN,
            "purpose": "Spende Sommerfest", "ref": "R3"}


def _tx_id(db, purpose):
    return db.execute('select id from "transaction" where purpose=?', (purpose,)).fetchone()[0]


def _member(db, name="Erika Muster", **kw):
    return create_member(db, name, "Aktiv", date(2026, 1, 15), **kw)


def _alloc(db, tx):
    return db.execute("select month, cents, status from allocation where tx_id=? order by month",
                      (tx,)).fetchall()


def _col(db, tx, col):
    return db.execute(f'select {col} from "transaction" where id=?', (tx,)).fetchone()[0]


def _cat(db, name):
    return db.execute("select id from category where name=?", (name,)).fetchone()[0]


def _assign(client, tx, member_id, **extra):
    data = {"member_id": str(member_id), "learn": "on", **extra}
    return client.post(f"/transactions/{tx}/member", data=data)


# --- Liste -------------------------------------------------------------------

def test_list_shows_transactions_masked_iban(client):
    _upload(client, [E_ERIKA, E_KIOSK])
    r = client.get("/transactions?year=2026")
    assert r.status_code == 200
    assert "Erika Muster" in r.text and "Material" in r.text
    assert "17,00 €" in r.text and "-5,00 €" in r.text
    assert "DE11…1111" in r.text
    assert OTHER_IBAN not in r.text and IBAN not in r.text
    assert "<caption" in r.text


def test_list_sums_filtered(client):
    _upload(client, [E_ERIKA, E_KIOSK, E_SPENDE])
    r = client.get("/transactions?year=2026")
    assert "42,00 €" in r.text and "-5,00 €" in r.text
    r = client.get("/transactions?year=2026&kind=out")
    assert "Erika" not in r.text and "Kiosk" in r.text
    assert "-5,00 €" in r.text and "42,00 €" not in r.text


def test_year_filter_and_default(client):
    _upload(client, [{**E_ERIKA, "ref": "A"}], day="2025-12-31")
    _upload(client, [{**E_KIOSK, "ref": "B"}], day="2026-03-01")
    assert "Erika" not in client.get("/transactions?year=2026").text
    assert "Erika" in client.get("/transactions?year=2025").text
    both = client.get("/transactions?year=all").text
    assert "Erika" in both and "Kiosk" in both
    _upload(client, [{**E_SPENDE, "ref": "C"}], day=f"{TODAY.year}-02-01")
    d = client.get("/transactions").text
    assert "Hans Spender" in d


def test_category_none_and_q(client, db):
    _upload(client, [E_ERIKA, E_KIOSK, E_SPENDE])
    cat = _cat(db, "Sonstiges")
    client.post(f"/transactions/{_tx_id(db, 'Material')}/category", data={"category_id": str(cat)})
    r = client.get("/transactions?year=2026&category=none")
    assert "Kiosk" not in r.text and "Hans Spender" in r.text
    r = client.get(f"/transactions?year=2026&category={cat}")
    assert "Kiosk" in r.text and "Hans Spender" not in r.text
    r = client.get("/transactions?year=2026&q=SOMMERFEST")
    assert "Hans Spender" in r.text and "Kiosk" not in r.text
    r = client.get("/transactions?year=2026&q=%3Cb%3Ex")
    assert "<b>x" not in r.text and "&lt;b&gt;x" in r.text


@pytest.mark.parametrize("qs", [
    "year=abc", "year=1899", "year=2101", "category=x", "category=99999999999999999999",
    "kind=x", "page=0", "page=abc", "page=100001", "page=-1", "q=" + "a" * 201,
])
def test_invalid_filters_422(client, qs):
    r = client.get("/transactions?" + qs)
    assert r.status_code == 422 and "Fehler" in r.text


def test_pagination(client, db):
    _upload(client, [E_KIOSK])
    db.executemany(
        'insert into "transaction"(account_id, booking_date, amount_cents, purpose, dedupe_key)'
        " values(1,'2026-04-01',100,?,?)", [(f"p{i}", f"k{i}") for i in range(150)])
    db.commit()
    p1 = client.get("/transactions?year=2026").text
    p2 = client.get("/transactions?year=2026&page=2").text
    assert p1.count('name="category_id"') == 100 and p2.count('name="category_id"') == 51
    assert "page=2" in p1


def test_script_in_name_escaped(client):
    _upload(client, [{**E_ERIKA, "name": "<script>alert(1)</script>"}])
    for url in ("/transactions?year=2026", "/unmatched"):
        t = client.get(url).text
        assert "<script>alert" not in t and "&lt;script&gt;" in t


def test_no_external_urls(client, db):
    _upload(client, [E_ERIKA, E_SPENDE])
    m = _member(db)
    tx = _tx_id(db, "Spende Sommerfest")
    _assign(client, tx, m)
    for url in ("/transactions?year=all", "/unmatched", f"/transactions/{tx}/allocate",
                "/categories"):
        assert not re.search(r"(src|href|action)=[\"']https?://", client.get(url).text), url


# --- Kategorie ---------------------------------------------------------------

def test_set_and_clear_category(client, db):
    _upload(client, [E_KIOSK])
    tx = _tx_id(db, "Material")
    cat = _cat(db, "Hauptverein")
    r = client.post(f"/transactions/{tx}/category", data={"category_id": str(cat)})
    assert r.status_code == 303 and _col(db, tx, "category_id") == cat
    r = client.post(f"/transactions/{tx}/category", data={"category_id": ""})
    assert r.status_code == 303 and _col(db, tx, "category_id") is None


@pytest.mark.parametrize("val", ["9999", "abc", "0", "99999999999999999999"])
def test_unknown_category_422(client, db, val):
    _upload(client, [E_KIOSK])
    tx = _tx_id(db, "Material")
    r = client.post(f"/transactions/{tx}/category", data={"category_id": val})
    assert r.status_code == 422 and _col(db, tx, "category_id") is None


def test_unknown_tx_404(client):
    assert client.post("/transactions/777/category", data={"category_id": ""}).status_code == 404
    assert client.post("/transactions/777/member", data={"member_id": ""}).status_code == 404


# --- Mitglied / Unklar -------------------------------------------------------

def test_assign_removes_from_unmatched(client, db):
    _upload(client, [E_SPENDE])
    m = _member(db)
    assert "Hans Spender" in client.get("/unmatched").text
    tx = _tx_id(db, "Spende Sommerfest")
    r = _assign(client, tx, m, next="/unmatched")
    assert r.status_code == 303 and r.headers["location"] == "/unmatched"
    assert _col(db, tx, "member_id") == m
    assert "Hans Spender" not in client.get("/unmatched").text


def test_learn_autoassigns_next_import(client, db):
    _upload(client, [E_SPENDE])
    m = _member(db)
    _assign(client, _tx_id(db, "Spende Sommerfest"), m)
    _upload(client, [{**E_SPENDE, "ref": "R9", "purpose": "Zweite"}], day="2026-04-01")
    assert _col(db, _tx_id(db, "Zweite"), "member_id") == m


def test_learn_unchecked_learns_nothing(client, db):
    _upload(client, [E_SPENDE])
    m = _member(db)
    tx = _tx_id(db, "Spende Sommerfest")
    r = client.post(f"/transactions/{tx}/member", data={"member_id": str(m)})
    assert r.status_code == 303
    assert db.execute("select count(*) from member_iban").fetchone()[0] == 0
    _upload(client, [{**E_SPENDE, "ref": "R9", "purpose": "Zweite"}], day="2026-04-01")
    assert _col(db, _tx_id(db, "Zweite"), "member_id") is None


def test_assign_outgoing_422(client, db):
    _upload(client, [E_KIOSK])
    m = _member(db)
    tx = _tx_id(db, "Material")
    r = _assign(client, tx, m)
    assert r.status_code == 422 and "Fehler" in r.text
    assert _col(db, tx, "member_id") is None


@pytest.mark.parametrize("val", ["9999", "abc", "-3", "99999999999999999999"])
def test_assign_unknown_member_422(client, db, val):
    _upload(client, [E_SPENDE])
    tx = _tx_id(db, "Spende Sommerfest")
    assert _assign(client, tx, val).status_code == 422
    assert _col(db, tx, "member_id") is None


def test_unassign_with_confirmed_allocation_422(client, db):
    _upload(client, [E_ERIKA])
    m = _member(db)
    tx = _tx_id(db, "Beitrag")
    _assign(client, tx, m)
    assert _alloc(db, tx)[0]["status"] == "confirmed"
    # #27: nur eine Bestätigung des Nutzers sperrt den Mitgliedswechsel
    month = _alloc(db, tx)[0]["month"]
    client.post(f"/transactions/{tx}/allocate", data={"month_0": month, "amount_0": "17,00"})
    r = client.post(f"/transactions/{tx}/member", data={"member_id": ""})
    assert r.status_code == 422 and "Fehler" in r.text
    assert _col(db, tx, "member_id") == m and len(_alloc(db, tx)) == 1


def test_unassign_with_auto_allocation_allowed(client, db):
    _upload(client, [E_ERIKA])
    m = _member(db)
    tx = _tx_id(db, "Beitrag")
    _assign(client, tx, m)
    assert _alloc(db, tx)[0]["status"] == "confirmed"
    r = client.post(f"/transactions/{tx}/member", data={"member_id": ""})
    assert r.status_code == 303
    assert _col(db, tx, "member_id") is None and _alloc(db, tx) == []


def test_member_select_marks_left_members(client, db):
    _upload(client, [E_SPENDE])
    m = _member(db, "Alt Mitglied")
    leave_member(db, m, date(2026, 2, 1))
    assert "Alt Mitglied (ausgetreten)" in client.get("/unmatched").text


# --- Weiterleitung -----------------------------------------------------------

@pytest.mark.parametrize("nxt,expected", [
    ("https://evil.example", "/transactions"), ("//evil.example", "/transactions"),
    ("/\\evil.example", "/transactions"), ("/members", "/transactions"),
    ("/transactions?year=2025&page=2", "/transactions?year=2025&page=2"),
    ("/unmatched", "/unmatched"), ("", "/transactions"),
])
def test_next_redirect_safe(client, db, nxt, expected):
    _upload(client, [E_KIOSK])
    tx = _tx_id(db, "Material")
    r = client.post(f"/transactions/{tx}/category", data={"category_id": "", "next": nxt})
    assert r.status_code == 303 and r.headers["location"] == expected


# --- Verteilung --------------------------------------------------------------

def _two(client, db):
    """Spende 25,00 (Vorschlag 02: 17,00 / 03: 8,00) einem Mitglied zugeordnet."""
    _upload(client, [E_SPENDE])
    set_check_start(db, "2026-01")  # Standard wäre 2026-03 (älteste Buchung)
    m = _member(db)
    tx = _tx_id(db, "Spende Sommerfest")
    _assign(client, tx, m)
    return m, tx


def test_allocate_get_renders(client, db):
    m, tx = _two(client, db)
    r = client.get(f"/transactions/{tx}/allocate")
    assert r.status_code == 200
    assert "2026-02" in r.text and "2026-03" in r.text and "vorgeschlagen" in r.text
    assert "17,00" in r.text and "25,00 €" in r.text and "Hans Spender" in r.text
    assert "Erika Muster" in r.text
    assert THIRD_IBAN not in r.text and "DE22…2222" not in r.text
    assert r.text.count('name="month_') == 5  # 2 vorhanden + 3 leer


def test_allocate_post_valid(client, db):
    m, tx = _two(client, db)
    r = client.post(f"/transactions/{tx}/allocate", data={
        "month_0": "2026-02", "amount_0": "17,00", "month_1": "2026-03", "amount_1": "8,00",
        "month_2": "", "amount_2": ""})
    assert r.status_code == 303 and r.headers["location"] == "/transactions"
    assert [(a["month"], a["cents"], a["status"]) for a in _alloc(db, tx)] == [
        ("2026-02", 1700, "confirmed"), ("2026-03", 800, "confirmed")]
    assert month_status(db, m, "2026-02", TODAY).status == "PAID"


def test_allocate_wrong_sum_keeps_input(client, db):
    m, tx = _two(client, db)
    before = [tuple(a) for a in _alloc(db, tx)]
    r = client.post(f"/transactions/{tx}/allocate", data={
        "month_0": "2026-02", "amount_0": "17,00", "month_1": "2026-04", "amount_1": "3,33"})
    assert r.status_code == 422 and "Fehler" in r.text
    assert 'value="2026-04"' in r.text and 'value="3,33"' in r.text
    assert [tuple(a) for a in _alloc(db, tx)] == before


@pytest.mark.parametrize("rows", [
    [("2026-02", "10,00"), ("2026-02", "15,00")],
    [("2026-13", "25,00")], [("26-01", "25,00")], [("2026-02", "abc")],
    [("2026-02", "0")], [("2026-02", "-5,00")], [("2026-02", "100.000,01")],
    [("2026-02", "")], [("", "25,00")], [],
])
def test_allocate_bad_rows_422(client, db, rows):
    m, tx = _two(client, db)
    before = [tuple(a) for a in _alloc(db, tx)]
    data = {}
    for i, (mo, am) in enumerate(rows):
        data[f"month_{i}"], data[f"amount_{i}"] = mo, am
    r = client.post(f"/transactions/{tx}/allocate", data=data)
    assert r.status_code == 422 and "Fehler" in r.text
    assert [tuple(a) for a in _alloc(db, tx)] == before


def test_allocate_outgoing_or_unassigned_rejected(client, db):
    _upload(client, [E_KIOSK, E_SPENDE])
    for p in ("Material", "Spende Sommerfest"):
        tx = _tx_id(db, p)
        assert client.get(f"/transactions/{tx}/allocate").status_code in (404, 422)
        r = client.post(f"/transactions/{tx}/allocate",
                        data={"month_0": "2026-02", "amount_0": "1,00"})
        assert r.status_code in (404, 422)
    assert db.execute("select count(*) from allocation").fetchone()[0] == 0


def test_stale_proposal_warning_and_repropose(client, db):
    _upload(client, [E_ERIKA, E_SPENDE])
    set_check_start(db, "2026-01")
    m = _member(db)
    b, a = _tx_id(db, "Spende Sommerfest"), _tx_id(db, "Beitrag")
    _assign(client, b, m)            # 25,00: Vorschlag 03: 17,00 + 02: 8,00
    assert [(x["month"], x["cents"], x["status"]) for x in _alloc(db, b)] == [
        ("2026-02", 800, "proposed"), ("2026-03", 1700, "proposed")]
    # "Neu vorschlagen" macht daraus einen Vorschlag des Nutzers (Fix-Runde 1, I3):
    # automatische Neuberechnungen fassen ihn nicht mehr an
    client.post(f"/transactions/{b}/allocate/propose")
    assert {r[0] for r in db.execute("select source from allocation where tx_id=?", (b,))} == {
        "manual"}
    _assign(client, a, m)            # 17,00 im März: März automatisch (#27)
    assert [(x["month"], x["status"]) for x in _alloc(db, a)] == [("2026-03", "confirmed")]
    assert "Monat bereits bezahlt" in client.get(f"/transactions/{b}/allocate").text
    assert "Monat bereits bezahlt" not in client.get(f"/transactions/{a}/allocate").text
    r = client.post(f"/transactions/{b}/allocate/propose")
    assert r.status_code == 303 and r.headers["location"] == f"/transactions/{b}/allocate"
    assert [(x["month"], x["cents"], x["status"]) for x in _alloc(db, b)] == [
        ("2026-02", 1700, "proposed"), ("2026-04", 800, "proposed")]


def test_repropose_never_confirms(client, db):
    _upload(client, [E_ERIKA])
    set_check_start(db, "2026-01")
    m = _member(db)
    tx = _tx_id(db, "Beitrag")
    _assign(client, tx, m)
    db.execute("delete from allocation where tx_id=?", (tx,))
    db.commit()
    r = client.post(f"/transactions/{tx}/allocate/propose")
    assert r.status_code == 303
    assert [tuple(r) for r in db.execute(
        "select month, status, source from allocation where tx_id=?", (tx,))] == [
        ("2026-03", "proposed", "manual")]


def test_clear_requires_confirm(client, db):
    _upload(client, [E_ERIKA])
    m = _member(db)
    tx = _tx_id(db, "Beitrag")
    _assign(client, tx, m)
    r = client.post(f"/transactions/{tx}/allocate/clear", data={})
    assert r.status_code == 422 and len(_alloc(db, tx)) == 1
    r = client.post(f"/transactions/{tx}/allocate/clear", data={"confirm": "yes"})
    assert r.status_code == 303 and r.headers["location"] == f"/transactions/{tx}/allocate"
    # Fix-Runde 1 (I2): Aufheben hinterlässt einen Vorschlag des Nutzers
    assert [(x["status"]) for x in _alloc(db, tx)] == ["proposed"]
    assert [r[0] for r in db.execute("select source from allocation where tx_id=?", (tx,))] == [
        "manual"]
    assert _col(db, tx, "member_id") == m


# --- Kategorien --------------------------------------------------------------

def test_categories_page_and_create(client, db):
    r = client.get("/categories")
    assert r.status_code == 200 and "Hauptverein" in r.text
    r = client.post("/categories", data={"form": "category", "name": "Neu", "kind": "out"})
    assert r.status_code == 303
    assert db.execute("select kind from category where name='Neu'").fetchone()[0] == "out"


@pytest.mark.parametrize("name,kind", [
    ("Hauptverein", "out"), ("hauptverein", "in"), ("ohne kategorie", "both"),
    ("Ohne Kategorie", "both"), ("", "in"), ("   ", "in"), ("x" * 101, "in"), ("Gut", "kaputt"),
])
def test_category_create_invalid(client, db, name, kind):
    n = db.execute("select count(*) from category").fetchone()[0]
    r = client.post("/categories", data={"form": "category", "name": name, "kind": kind})
    assert r.status_code == 422 and "Fehler" in r.text
    assert db.execute("select count(*) from category").fetchone()[0] == n


def _rule(client, **kw):
    data = {"form": "rule", "field": "purpose", "contains": "Material",
            "priority": "100", "min_amount": "", "max_amount": "", **kw}
    return client.post("/categories", data=data)


def test_rule_valid_and_listed(client, db):
    cat = _cat(db, "Hauptverein")
    r = _rule(client, category_id=str(cat), min_amount="1,50", max_amount="20", priority="5")
    assert r.status_code == 303
    row = db.execute("select * from category_rule").fetchone()
    assert (row["field"], row["contains"], row["priority"], row["min_cents"],
            row["max_cents"]) == ("purpose", "Material", 5, 150, 2000)
    assert "Material" in client.get("/categories").text


@pytest.mark.parametrize("kw", [
    {"contains": ""}, {"contains": "   "}, {"contains": "x" * 201}, {"field": "amount_cents"},
    {"min_amount": "5", "max_amount": "4"}, {"max_amount": "100.000,01"}, {"min_amount": "abc"},
    {"min_amount": "-1"}, {"priority": "-1"}, {"priority": "10001"}, {"priority": "x"},
    {"category_id": "9999"}, {"category_id": ""},
])
def test_rule_invalid(client, db, kw):
    kw.setdefault("category_id", str(_cat(db, "Hauptverein")))
    r = _rule(client, **kw)
    assert r.status_code == 422 and "Fehler" in r.text
    assert db.execute("select count(*) from category_rule").fetchone()[0] == 0


def test_apply_keeps_manual_without_overwrite(client, db):
    _upload(client, [E_KIOSK, {**E_KIOSK, "ref": "R5", "purpose": "Material 2"}])
    manual, haupt = _cat(db, "Sonstiges"), _cat(db, "Hauptverein")
    t1, t2 = _tx_id(db, "Material"), _tx_id(db, "Material 2")
    client.post(f"/transactions/{t1}/category", data={"category_id": str(manual)})
    _rule(client, category_id=str(haupt))
    r = client.post("/categories/apply", data={})
    assert r.status_code == 303 and "applied=1" in r.headers["location"]
    assert _col(db, t1, "category_id") == manual and _col(db, t2, "category_id") == haupt
    assert "1 Buchung" in client.get(r.headers["location"]).text


def test_apply_overwrite_needs_confirm(client, db):
    _upload(client, [E_KIOSK])
    manual, haupt = _cat(db, "Sonstiges"), _cat(db, "Hauptverein")
    t1 = _tx_id(db, "Material")
    client.post(f"/transactions/{t1}/category", data={"category_id": str(manual)})
    _rule(client, category_id=str(haupt))
    r = client.post("/categories/apply", data={"overwrite": "on"})
    assert r.status_code == 422 and _col(db, t1, "category_id") == manual
    r = client.post("/categories/apply", data={"overwrite": "on", "confirm": "yes"})
    assert r.status_code == 303 and _col(db, t1, "category_id") == haupt


# --- Sicherheit --------------------------------------------------------------

BAD_IDS = ["0", "-1", "99999999999999999999"]
ID_ROUTES = [("get", "/allocate"), ("post", "/category"), ("post", "/member"),
             ("post", "/allocate"), ("post", "/allocate/propose"), ("post", "/allocate/clear")]


@pytest.mark.parametrize("bad", BAD_IDS)
@pytest.mark.parametrize("method,suffix", ID_ROUTES)
def test_bad_ids_404(client, method, suffix, bad):
    r = getattr(client, method)(f"/transactions/{bad}{suffix}")
    assert r.status_code == 404


@pytest.mark.parametrize("path", [
    "/transactions/1/category", "/transactions/1/member", "/transactions/1/allocate",
    "/transactions/1/allocate/propose", "/transactions/1/allocate/clear",
    "/categories", "/categories/apply",
])
def test_foreign_origin_403(client, path):
    r = client.post(path, data={}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


# --- I1: "Absender merken" nur auf /unmatched vorbelegt ------------------------

_LEARN = re.compile(r'<input type="checkbox" name="learn" value="on"( checked)?>')


def test_learn_checkbox_checked_on_unmatched(client):
    _upload(client, [E_ERIKA])
    boxes = _LEARN.findall(client.get("/unmatched").text)
    assert boxes and all(b == " checked" for b in boxes)


def test_learn_checkbox_not_checked_on_transactions(client):
    _upload(client, [E_ERIKA])
    boxes = _LEARN.findall(client.get("/transactions?year=all").text)
    assert boxes and all(b == "" for b in boxes)


def test_siblings_web_second_child_learn_leaves_feb_unmatched(client, db):
    set_check_start(db, "2026-01")
    a, b = _member(db, "Kind A"), _member(db, "Kind B")
    jan = [dict(E_ERIKA, ref="J1"), dict(E_ERIKA, ref="J2", purpose="Beitrag 2")]
    _upload(client, jan, day="2026-01-05")
    t1, t2 = _tx_id(db, "Beitrag"), _tx_id(db, "Beitrag 2")
    _assign(client, t1, a)
    _assign(client, t2, b)
    feb = [dict(E_ERIKA, ref="F1", purpose="Feb 1"), dict(E_ERIKA, ref="F2", purpose="Feb 2")]
    r = _upload(client, feb, day="2026-02-05")
    assert "Automatisch zugeordnet: 0" in r.text
    un = client.get("/unmatched").text
    assert "Feb 1" in un and "Feb 2" in un


# --- I4: offene Vorschläge finden ---------------------------------------------

def test_import_result_shows_open_proposals(client, db):
    _upload(client, [E_SPENDE])
    set_check_start(db, "2026-01")
    m = _member(db)
    _assign(client, _tx_id(db, "Spende Sommerfest"), m)  # 25,00 -> Vorschlag
    r = _upload(client, [E_KIOSK], day="2026-03-02")
    assert "1 offene Vorschläge insgesamt" in r.text
    assert "automatisch verteilt: 0, zur Prüfung: 0" in r.text  # Kiosk ist kein Eingang
    assert 'href="/transactions?year=all&amp;proposed=1"' in r.text


def test_transactions_filter_proposed(client, db):
    _upload(client, [E_ERIKA, E_SPENDE, E_KIOSK])
    set_check_start(db, "2026-01")
    m = _member(db)
    spende = _tx_id(db, "Spende Sommerfest")
    _assign(client, spende, m)                       # Vorschlag
    t = client.get("/transactions?year=all&proposed=1").text
    assert "Spende Sommerfest" in t
    assert "Material" not in t and ">Beitrag<" not in t
    assert "Buchungen: <strong>1</strong>" in t
    t_all = client.get("/transactions?year=all").text
    assert "Material" in t_all


@pytest.mark.parametrize("bad", ["2", "yes", "0", "1 or 1=1"])
def test_transactions_filter_proposed_invalid(client, bad):
    r = client.get("/transactions", params={"proposed": bad})
    assert r.status_code == 422


def test_proposed_filter_kept_in_page_links(client, db):
    from urllib.parse import parse_qs, urlsplit
    import kassivo.web.transactions_routes as tr
    _upload(client, [E_SPENDE])
    m = _member(db)
    _assign(client, _tx_id(db, "Spende Sommerfest"), m)
    old = tr.PAGE_SIZE
    try:
        tr.PAGE_SIZE = 1
        _upload(client, [dict(E_SPENDE, ref="R9", purpose="Spende 2")])
        _assign(client, _tx_id(db, "Spende 2"), m)
        t = client.get("/transactions?year=all&proposed=1").text
        nxt = re.search(r'href="(/transactions\?[^"]*page=2[^"]*)"', t)[1].replace("&amp;", "&")
        assert parse_qs(urlsplit(nxt).query)["proposed"] == ["1"]
    finally:
        tr.PAGE_SIZE = old


def test_open_proposals_links(client):
    for path in ("/unmatched", "/transactions"):
        t = client.get(path).text
        assert 'href="/transactions?year=all&amp;proposed=1"' in t and "Offene Vorschläge" in t


# --- I5: Unklar automatisch zuordnen, Lernen wirkt auf alte Zahlungen ----------

def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _unmatched_ids(db):
    return {r[0] for r in db.execute(
        'select id from "transaction" where amount_cents > 0 and member_id is null')}


def _three_months(client, iban=OTHER_IBAN, prefix="M"):
    """Erstimport über drei Monate von einem Absender, nichts bekannt."""
    entries = [dict(E_ERIKA, cp_iban=iban, ref=f"{prefix}{i}", purpose=f"{prefix} {i}",
                    booking_date=f"2026-0{i}-05") for i in (1, 2, 3)]
    _upload(client, entries)
    return entries


def test_unmatched_auto_button_assigns_exactly_matching(client, db):
    _three_months(client)
    _upload(client, [dict(E_SPENDE, ref="S1")])
    m = _member(db, iban=OTHER_IBAN)          # IBAN erst nach dem Import bekannt
    assert "Unklare automatisch zuordnen" in client.get("/unmatched").text
    r = client.post("/unmatched/auto")
    assert r.status_code == 303 and r.headers["location"] == "/unmatched?note=auto&n=3"
    for i in (1, 2, 3):
        assert _col(db, _tx_id(db, f"M {i}"), "member_id") == m
    assert _col(db, _tx_id(db, "Spende Sommerfest"), "member_id") is None
    t = client.get(r.headers["location"]).text
    assert "3 unklare Zahlungen automatisch zugeordnet." in _text(t)


def test_unmatched_auto_skips_ambiguous(client, db):
    _three_months(client)
    _member(db, "Kind A", iban=OTHER_IBAN)
    db.execute("insert into member_iban(iban, member_id) values(?, null)", (OTHER_IBAN,))
    db.commit()                              # mehrdeutig: kein Rückgriff auf member.iban
    r = client.post("/unmatched/auto")
    assert r.headers["location"] == "/unmatched?note=auto&n=0"
    assert all(_col(db, _tx_id(db, f"M {i}"), "member_id") is None for i in (1, 2, 3))


def test_unmatched_auto_foreign_origin_changes_nothing(client, db):
    _three_months(client)
    _member(db, iban=OTHER_IBAN)
    before = _unmatched_ids(db)
    r = client.post("/unmatched/auto", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert _unmatched_ids(db) == before


def test_learn_cascades_to_other_unmatched_same_iban(client, db):
    _three_months(client)
    _upload(client, [dict(E_SPENDE, ref="S1")])
    m = _member(db)
    r = _assign(client, _tx_id(db, "M 1"), m, next="/unmatched")
    assert r.status_code == 303
    assert r.headers["location"] == "/unmatched?note=learned&n=2"
    assert _col(db, _tx_id(db, "M 2"), "member_id") == m
    assert _col(db, _tx_id(db, "M 3"), "member_id") == m
    assert _col(db, _tx_id(db, "Spende Sommerfest"), "member_id") is None
    t = _text(client.get(r.headers["location"]).text)
    assert "2 weitere Zahlungen mit derselben Absender-IBAN wurden automatisch zugeordnet." in t


def test_learn_cascade_keeps_existing_query_in_next(client, db):
    _three_months(client)
    m = _member(db)
    r = _assign(client, _tx_id(db, "M 1"), m, next="/transactions?year=all")
    assert r.headers["location"] == "/transactions?year=all&note=learned&n=2"


def test_learn_off_no_cascade(client, db):
    _three_months(client)
    m = _member(db)
    tx = _tx_id(db, "M 1")
    r = client.post(f"/transactions/{tx}/member", data={"member_id": str(m), "next": "/unmatched"})
    assert r.headers["location"] == "/unmatched"
    assert _col(db, _tx_id(db, "M 2"), "member_id") is None


def test_learn_cascade_skipped_when_ambiguous(client, db):
    _three_months(client)
    a, b = _member(db, "Kind A"), _member(db, "Kind B")
    db.execute("insert into member_iban(iban, member_id) values(?,?)", (OTHER_IBAN, a))
    db.commit()
    r = _assign(client, _tx_id(db, "M 1"), b, next="/unmatched")  # IBAN wird mehrdeutig
    # Hinweis auf die Mehrdeutigkeit, nichts weiter zugeordnet
    assert r.headers["location"] == "/unmatched?note=ambiguous&n=0"
    assert _col(db, _tx_id(db, "M 2"), "member_id") is None
    assert _col(db, _tx_id(db, "M 3"), "member_id") is None


@pytest.mark.parametrize("q", ["note=evil", "note=auto&n=abc", "note=<script>", "n=5"])
def test_unknown_note_codes_show_nothing(client, q):
    t = client.get(f"/unmatched?{q}").text
    assert "automatisch zugeordnet." not in t and "<script>" not in t.split("<main ")[1]


def test_allocation_rejects_arabic_indic_digits(client, db):
    m, tx = _two(client, db)
    before = _alloc(db, tx)
    r = client.post(f"/transactions/{tx}/allocate", data={
        "month_0": "2026-02", "amount_0": "١٧,٠٠", "month_1": "2026-03", "amount_1": "8,00"})
    assert r.status_code == 422
    assert _alloc(db, tx) == before


@pytest.mark.parametrize("raw", ["sNaN", "1E999999", "+0.00", "0.001"])
def test_import_bad_amount_is_422_not_500(client, db, raw):
    data = make_camt(IBAN, 0, [dict(E_ERIKA, amount_raw=raw)], "2026-03-01", with_balances=False)
    r = client.post("/import", files={"file": ("x.xml", data, "application/xml")})
    assert r.status_code == 422 and "Es wurde nichts importiert" in r.text
    assert db.execute('select count(*) from "transaction"').fetchone()[0] == 0


# --- Fix-Runde 2 (N1): Mehrdeutigkeit auch über member.iban ---------------------

AMBIG_NOTE = ("Zugeordnet. Die Absender-IBAN gehört mehreren Mitgliedern und wird"
              " künftig nicht automatisch zugeordnet.")


def _kid(db, name, **kw):
    # Eintritt Dezember 2025 -> Beitrag ab 2026-01
    return create_member(db, name, "Aktiv", date(2025, 12, 10), **kw)


def test_siblings_member_iban_learn_on_unmatched_stays_ambiguous(client, db):
    """sim4: beide Kinder haben die Eltern-IBAN in den Stammdaten."""
    set_check_start(db, "2026-01")
    a = _kid(db, "Kind A", iban=OTHER_IBAN)
    b = _kid(db, "Kind B", iban=OTHER_IBAN)
    entries = [dict(E_ERIKA, ref=f"P{i}", purpose=f"P {i}", booking_date=d)
               for i, d in enumerate(("2026-01-03", "2026-01-04", "2026-02-03", "2026-02-04"))]
    _upload(client, entries)
    ids = [_tx_id(db, f"P {i}") for i in range(4)]
    assert all(_col(db, t, "member_id") is None for t in ids)  # Import: mehrdeutig
    r = _assign(client, ids[0], a, next="/unmatched")
    assert r.status_code == 303
    assert r.headers["location"] == "/unmatched?note=ambiguous&n=0"
    assert AMBIG_NOTE in _text(client.get(r.headers["location"]).text)
    assert db.execute("select member_id from member_iban where iban=?",
                      (OTHER_IBAN,)).fetchone()[0] is None
    assert all(_col(db, t, "member_id") is None for t in ids[1:])
    assert db.execute("select count(*) from allocation where tx_id<>?",
                      (ids[0],)).fetchone()[0] == 0
    assert db.execute("select count(*) from allocation where member_id=?",
                      (b,)).fetchone()[0] == 0
    feb = date(2026, 3, 10)
    assert month_status(db, a, "2026-02", feb).status == "OVERDUE"
    assert month_status(db, b, "2026-02", feb).status == "OVERDUE"
    # späterer Lauf "Unklare automatisch zuordnen" ordnet auch nichts zu
    assert client.post("/unmatched/auto").headers["location"] == "/unmatched?note=auto&n=0"


def test_single_payer_learn_cascade_confirms_booking_months(client, db):
    set_check_start(db, "2026-01")
    m = _kid(db, "Erika Muster")
    _three_months(client)
    r = _assign(client, _tx_id(db, "M 1"), m, next="/unmatched")
    assert r.headers["location"] == "/unmatched?note=learned&n=2"
    for i in (1, 2, 3):
        tx = _tx_id(db, f"M {i}")
        assert _col(db, tx, "member_id") == m
        assert [tuple(x) for x in _alloc(db, tx)] == [(f"2026-0{i}", 1700, "confirmed")]


# --- Issue #28: Unklar zeigt nur Eingänge, die ein Mitglied brauchen -------------

E_ZINS = {"amount": 646, "purpose": "Abrechnung 30.09.2026 Zinsen für Guthaben 8,77+ "
          "Kapitalertragsteuer 2,19- Abrechnung 6,46+", "ref": "Z1", "name": None}


def _zins_cat(db, name="Zinsen"):
    from kassivo.categories import create_category
    cid = create_category(db, name, "in")
    db.commit()
    return cid


def _categorize(client, tx, cat, **kw):
    return client.post(f"/transactions/{tx}/category",
                       data={"category_id": str(cat), "next": "/unmatched"}, **kw)


def test_unmatched_zins_example_appears_first_and_disappears(client, db):
    _upload(client, [dict(E_ERIKA, booking_date="2026-03-01", ref="E1"),
                     dict(E_ZINS, booking_date="2026-09-30")])
    tx = _tx_id(db, E_ZINS["purpose"])
    page = client.get("/unmatched").text
    assert page.index("Zinsen für Guthaben") < page.index("Erika Muster")
    r = _categorize(client, tx, _zins_cat(db))
    assert r.status_code == 303 and r.headers["location"] == "/unmatched"
    page = client.get("/unmatched").text
    assert "Zinsen für Guthaben" not in page and "Erika Muster" in page


def test_unmatched_page_texts_and_category_form(client, db):
    _upload(client, [E_ERIKA])
    _zins_cat(db)
    db.execute("insert into category(name, kind) values('Ausgabe XY', 'out')")
    db.commit()
    t = _text(client.get("/unmatched").text)
    assert "Kein Mitglied nötig" in t and "Als erledigt kategorisieren" in t
    assert ("Zahlungseingänge ohne Mitglied. Gehört die Zahlung zu einem Mitglied, ordnen Sie sie zu. "
            "Zinsen, Spenden und ähnliche Posten brauchen kein Mitglied: Wählen Sie eine Kategorie, "
            "dann verschwindet die Zahlung aus dieser Liste.") in t
    assert "Zinsen" in t and "Ausgabe XY" not in t


def test_unmatched_empty_state_unchanged(client, db):
    assert "Alle Zahlungseingänge sind zugeordnet." in client.get("/unmatched").text


def test_unmatched_escapes_names(client, db):
    _upload(client, [dict(E_SPENDE, name="<b>Boese</b>")])
    page = client.get("/unmatched").text
    assert "<b>Boese" not in page and "&lt;b&gt;Boese" in page


def test_unmatched_auto_ignores_categorized_non_member(client, db):
    _upload(client, [dict(E_ERIKA, ref="A1", purpose="Beitrag A")])
    tx = _tx_id(db, "Beitrag A")
    _categorize(client, tx, _zins_cat(db))
    _member(db, iban=OTHER_IBAN)
    r = client.post("/unmatched/auto")
    assert r.headers["location"] == "/unmatched?note=auto&n=0"
    assert _col(db, tx, "member_id") is None


def test_learn_cascade_ignores_categorized_non_member(client, db):
    _upload(client, [dict(E_ERIKA, ref="A1", purpose="Beitrag A"),
                     dict(E_ERIKA, ref="A2", purpose="Beitrag B"),
                     dict(E_ERIKA, ref="A3", purpose="Zins C")])
    done = _tx_id(db, "Zins C")
    _categorize(client, done, _zins_cat(db))
    m = _member(db)
    r = _assign(client, _tx_id(db, "Beitrag A"), m)
    assert r.headers["location"] == "/unmatched?note=learned&n=1" or "learned" in r.headers["location"]
    assert _col(db, _tx_id(db, "Beitrag B"), "member_id") == m
    assert _col(db, done, "member_id") is None


def test_import_rule_keeps_interest_out_of_unmatched(client, db):
    from kassivo.categories import add_rule
    cid = _zins_cat(db)
    add_rule(db, cid, "purpose", "Zinsen für Guthaben")
    db.commit()
    _upload(client, [E_ZINS])
    tx = _tx_id(db, E_ZINS["purpose"])
    assert _col(db, tx, "category_id") == cid
    assert "Zinsen für Guthaben" not in client.get("/unmatched").text


def test_category_post_foreign_origin_403(client, db):
    _upload(client, [E_ERIKA])
    tx = _tx_id(db, "Beitrag")
    r = _categorize(client, tx, _zins_cat(db), headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert _col(db, tx, "category_id") is None
