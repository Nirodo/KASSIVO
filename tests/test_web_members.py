"""Weboberfläche: Mitglieder, Einstellungen, Import. Nur erfundene Daten."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from kassivo.db import connect
from kassivo.dues import get_check_start
from kassivo.fees import fee_at
from kassivo.members import create_member, leave_member, list_members, type_at, update_member
from kassivo.web.app import create_app
from tests.helpers import IBAN, OTHER_IBAN, make_camt, make_zip


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "t.db"


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path), follow_redirects=False)


@pytest.fixture
def db(db_path, client):
    c = connect(db_path)
    yield c
    c.close()


def _count(db, table):
    return db.execute(f'select count(*) from "{table}"').fetchone()[0]


NEW = {"name": "Erika Muster", "type_name": "Aktiv", "eintritt": "2026-01-15"}


def _zip(entries=None, day="2026-03-01"):
    entries = entries if entries is not None else [
        {"amount": 1700, "name": "Erika Muster", "cp_iban": OTHER_IBAN,
         "purpose": "Beitrag", "ref": "R1"},
        {"amount": -500, "name": "Kiosk", "purpose": "Material", "ref": "R2"},
    ]
    return make_zip({"a.xml": make_camt(IBAN, 10000, entries, day)})


def _upload(client, data, name="geheim-name.zip"):
    return client.post("/import", files={"file": (name, data, "application/octet-stream")})


# --- Grundlagen / Sicherheit -------------------------------------------------

def test_root_redirects_to_members(client):
    r = client.get("/")
    assert r.status_code in (302, 303, 307) and r.headers["location"] == "/members"


def test_nav_links(client):
    html = client.get("/members").text
    for href in ("/dues", "/members", "/transactions", "/unmatched", "/categories",
                 "/forecast", "/reports", "/import", "/settings/fees"):
        assert f'href="{href}"' in html


def test_security_headers(client):
    r = client.get("/members")
    assert r.headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "same-origin"


def test_bad_host_rejected(client):
    r = client.get("/members", headers={"host": "evil.example"})
    assert r.status_code == 400
    assert client.get("/members", headers={"host": "localhost:8000"}).status_code == 200
    assert client.get("/members", headers={"host": "127.0.0.1:8000"}).status_code == 200


def test_foreign_origin_rejected(client, db):
    r = client.post("/members/new", data=NEW, headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    assert _count(db, "member") == 0
    ok = client.post("/members/new", data=NEW, headers={"origin": "http://testserver"})
    assert ok.status_code == 303


def test_no_external_resources_or_inline_code(client):
    for url in ("/members", "/members/new", "/settings/fees", "/import"):
        html = client.get(url).text
        assert "http://" not in html and "https://" not in html
        assert "<script" not in html and "style=" not in html and "<style" not in html
    assert client.get("/static/style.css").status_code == 200


def test_unknown_page_german_404(client):
    r = client.get("/gibt-es-nicht")
    assert r.status_code == 404 and "nicht gefunden" in r.text


# --- Mitgliederliste ---------------------------------------------------------

def test_list_shows_member_masked_iban(client, db):
    create_member(db, "Erika Muster", "Aktiv", date(2026, 1, 15), iban="de11 2222 3333 4444 5555 66")
    html = client.get("/members").text
    assert "Erika Muster" in html and "Aktiv" in html and "2026-01-15" in html
    assert "DE11…5566" in html
    assert "DE11222233334444555566" not in html and "2222" not in html
    assert "Ausgetreten" not in html


def test_list_filter_and_austritt_status(client, db):
    create_member(db, "Erika Muster", "Aktiv", date(2026, 1, 15))
    b = create_member(db, "Bernd Beispiel", "Passiv", date(2026, 1, 15))
    leave_member(db, b, date(2026, 6, 30))
    html = client.get("/members?q=ERIKA").text
    assert "Erika Muster" in html and "Bernd" not in html
    assert "Ausgetreten" in client.get("/members?q=bernd").text


def test_name_escaped(client, db):
    create_member(db, "<script>x</script>", "Aktiv", date(2026, 1, 15))
    for url in ("/members", "/members/1"):
        html = client.get(url).text
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;x&lt;/script&gt;" in html


# --- Anlegen -----------------------------------------------------------------

def test_create_member(client, db):
    r = client.post("/members/new", data={**NEW, "iban": " de11 2222 ", "city": "Ort"})
    assert r.status_code == 303 and r.headers["location"] == "/members"
    m = list_members(db)[0]
    assert m["name"] == "Erika Muster" and m["iban"] == "DE112222" and m["city"] == "Ort"
    assert m["fee_start"] == "2026-02"
    assert type_at(db, m["id"], "2026-02") == "Aktiv"


def test_create_with_fee_start(client, db):
    client.post("/members/new", data={**NEW, "fee_start": "2026-01"})
    assert list_members(db)[0]["fee_start"] == "2026-01"


@pytest.mark.parametrize("override", [
    {"name": ""}, {"name": "   "}, {"eintritt": ""},
    {"eintritt": "2026-13-40"}, {"eintritt": "1800-01-01"}, {"eintritt": "2101-01-01"},
    {"eintritt": "20260115"}, {"fee_start": "2026-13"}, {"fee_start": "2026-1"},
    {"type_name": "Gibtsnicht"},
])
def test_create_invalid_rejected(client, db, override):
    r = client.post("/members/new", data={**NEW, **override})
    assert r.status_code == 422
    assert _count(db, "member") == 0
    assert "Fehler" in r.text


def test_create_error_keeps_input(client):
    r = client.post("/members/new", data={**NEW, "eintritt": "2026-13-40"})
    assert 'value="Erika Muster"' in r.text


def test_create_form_has_labels(client):
    html = client.get("/members/new").text
    for f in ("name", "type_name", "eintritt", "street", "zip", "city", "email",
              "phone", "iban", "fee_start"):
        assert f'for="{f}"' in html and f'id="{f}"' in html


# --- Bearbeiten --------------------------------------------------------------

@pytest.fixture
def mid(db):
    return create_member(db, "Erika Muster", "Aktiv", date(2026, 1, 15))


def test_edit_updates_stammdaten(client, db, mid):
    assert client.get(f"/members/{mid}").status_code == 200
    r = client.post(f"/members/{mid}", data={"name": "Erika Neu", "iban": "de00 1234", "city": "X"})
    assert r.status_code == 303 and r.headers["location"].startswith(f"/members/{mid}")
    row = db.execute("select * from member where id=?", (mid,)).fetchone()
    assert row["name"] == "Erika Neu" and row["iban"] == "DE001234" and row["city"] == "X"
    assert row["fee_start"] == "2026-02" and row["eintritt"] == "2026-01-15"


def test_edit_empty_name_rejected(client, db, mid):
    r = client.post(f"/members/{mid}", data={"name": ""})
    assert r.status_code == 422
    assert db.execute("select name from member").fetchone()[0] == "Erika Muster"


def test_edit_form_shows_stored_iban(client, db, mid):
    update_member(db, mid, iban="DE11222233334444555566")
    assert "DE11222233334444555566" in client.get(f"/members/{mid}").text


def test_change_type(client, db, mid):
    r = client.post(f"/members/{mid}/type", data={"type_name": "Passiv", "valid_from": "2026-09"})
    assert r.status_code == 303
    assert type_at(db, mid, "2026-08") == "Aktiv" and type_at(db, mid, "2026-09") == "Passiv"


@pytest.mark.parametrize("data", [
    {"type_name": "Passiv", "valid_from": "2026-13"},
    {"type_name": "Passiv", "valid_from": ""},
    {"type_name": "Nope", "valid_from": "2026-09"},
])
def test_change_type_invalid(client, db, mid, data):
    assert client.post(f"/members/{mid}/type", data=data).status_code == 422
    assert db.execute("select count(*) from member_type_history").fetchone()[0] == 1


def test_leave(client, db, mid):
    r = client.post(f"/members/{mid}/leave", data={"austritt": "2026-06-30"})
    assert r.status_code == 303
    assert db.execute("select austritt from member").fetchone()[0] == "2026-06-30"


@pytest.mark.parametrize("bad", ["", "2026-13-40", "1800-01-01", "abc"])
def test_leave_invalid(client, db, mid, bad):
    assert client.post(f"/members/{mid}/leave", data={"austritt": bad}).status_code == 422
    assert db.execute("select austritt from member").fetchone()[0] is None


def test_unknown_member_404(client):
    assert client.get("/members/999").status_code == 404
    assert client.post("/members/999", data={"name": "x"}).status_code == 404
    assert client.post("/members/999/type", data={"type_name": "Aktiv", "valid_from": "2026-01"}).status_code == 404
    assert client.post("/members/999/leave", data={"austritt": "2026-01-01"}).status_code == 404


# --- Einstellungen -----------------------------------------------------------

def test_fees_page_shows_current_and_history(client):
    html = client.get("/settings/fees").text
    assert "17,00 €" in html and "10,00 €" in html and "1900-01" in html


def test_set_fee(client, db):
    r = client.post("/settings/fees", data={"form": "fee", "type_name": "Aktiv",
                                            "amount": "19,50", "valid_from": "2026-07"})
    assert r.status_code == 303
    assert fee_at(db, "Aktiv", "2026-06") == 1700
    assert fee_at(db, "Aktiv", "2026-07") == 1950
    assert "19,50 €" in client.get("/settings/fees").text


@pytest.mark.parametrize("amount", ["0", "abc", "-5", "", "0,00", "1,234"])
def test_set_fee_invalid(client, db, amount):
    r = client.post("/settings/fees", data={"form": "fee", "type_name": "Aktiv",
                                            "amount": amount, "valid_from": "2026-07"})
    assert r.status_code == 422
    assert _count(db, "fee_rate") == 2


@pytest.mark.parametrize("data", [
    {"type_name": "Aktiv", "amount": "5", "valid_from": "2026-13"},
    {"type_name": "Nope", "amount": "5", "valid_from": "2026-01"},
])
def test_set_fee_invalid_other(client, db, data):
    assert client.post("/settings/fees", data={"form": "fee", **data}).status_code == 422
    assert _count(db, "fee_rate") == 2


def test_check_start(client, db):
    r = client.post("/settings/fees", data={"form": "check_start", "check_start": "2025-10"})
    assert r.status_code == 303
    assert get_check_start(db) == "2025-10"
    assert "2025-10" in client.get("/settings/fees").text


@pytest.mark.parametrize("bad", ["2025-13", "", "25-10", "abc"])
def test_check_start_invalid(client, db, bad):
    r = client.post("/settings/fees", data={"form": "check_start", "check_start": bad})
    assert r.status_code == 422
    assert get_check_start(db) is None


# --- Import ------------------------------------------------------------------

def test_import_page_hint(client):
    html = client.get("/import").text
    assert "CAMT" in html and ".zip,.xml" in html


def test_import_zip_and_auto_assign(client, db):
    create_member(db, "Erika Muster", "Aktiv", date(2026, 1, 15), iban=OTHER_IBAN.lower())
    r = _upload(client, _zip())
    assert r.status_code == 200
    assert "Neu: 2" in r.text and "Duplikate: 0" in r.text
    assert "Automatisch zugeordnet: 1" in r.text
    assert "Automatisch kategorisiert: " in r.text
    assert db.execute('select count(*) from "transaction" where member_id is not null').fetchone()[0] == 1
    assert "geheim-name" not in r.text
    assert db.execute("select filename from import_batch").fetchone()[0] in ("upload.zip", "upload.xml")


def test_import_twice_duplicates(client, db):
    data = _zip()
    _upload(client, data)
    r = _upload(client, data)
    assert r.status_code == 200
    assert "Neu: 0" in r.text and "Duplikate: 2" in r.text
    assert _count(db, "transaction") == 2


def test_import_plain_xml(client, db):
    xml = make_camt(IBAN, 0, [{"amount": 100, "name": "A", "ref": "X"}], "2026-03-01")
    r = _upload(client, b"\xef\xbb\xbf" + xml, name="x.xml")
    assert r.status_code == 200 and "Neu: 1" in r.text
    assert db.execute("select filename from import_batch").fetchone()[0] == "upload.xml"


def test_import_garbage(client, db):
    r = _upload(client, bytes(range(256)) * 10)
    assert r.status_code == 422 and "Dateityp nicht erkannt" in r.text
    assert _count(db, "transaction") == 0 and _count(db, "import_batch") == 0
    assert _upload(client, b"").status_code == 422


def test_import_broken_xml_in_zip(client, db):
    r = _upload(client, make_zip({"a.xml": b"<Document><nope"}))
    assert r.status_code == 422 and "Traceback" not in r.text
    assert _count(db, "transaction") == 0


def test_import_broken_zip_and_broken_xml(client, db):
    assert _upload(client, b"PK\x03\x04garbage").status_code == 422
    assert _upload(client, b"<not-camt").status_code == 422
    assert _count(db, "transaction") == 0


def test_import_no_file(client):
    assert client.post("/import").status_code == 422


def test_import_too_large(client, db, monkeypatch):
    monkeypatch.setattr("kassivo.web.import_routes.MAX_UPLOAD_BYTES", 100)
    r = _upload(client, _zip())
    assert r.status_code == 413 and "zu groß" in r.text
    assert _count(db, "transaction") == 0 and _count(db, "import_batch") == 0


def test_import_warning_has_no_full_iban(client):
    # falscher Schlusssaldo erzeugt eine Warnung, nur mit maskierter IBAN
    data = make_zip({"a.xml": make_camt(IBAN, 0, [{"amount": 100, "ref": "W"}], "2026-03-01", closing=999)})
    r = _upload(client, data)
    assert r.status_code == 200
    assert IBAN not in r.text


# --- Fix round 1 -------------------------------------------------------------

def test_referrer_policy_same_origin(client):
    assert client.get("/members").headers["referrer-policy"] == "same-origin"


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000"])
def test_post_with_real_origin_ok(client, db, host):
    r = client.post("/members/new", data=NEW,
                    headers={"host": host, "origin": f"http://{host}"})
    assert r.status_code == 303
    assert _count(db, "member") == 1


def test_origin_null_rejected(client, db):
    r = client.post("/members/new", data=NEW, headers={"origin": "null"})
    assert r.status_code == 403 and _count(db, "member") == 0


@pytest.mark.parametrize("path", ["/members/99999999999999999999", "/members/0", "/members/-1"])
def test_huge_or_bad_member_id_404(client, path):
    assert client.get(path).status_code == 404
    assert client.post(path, data={"name": "x"}).status_code == 404
    assert client.post(path + "/type", data={"type_name": "Aktiv", "valid_from": "2026-01"}).status_code == 404
    assert client.post(path + "/leave", data={"austritt": "2026-01-01"}).status_code == 404


@pytest.mark.parametrize("amount", ["9" * 25, "100000,01", "0,00", "-5", "abc"])
def test_fee_range_rejected(client, db, amount):
    r = client.post("/settings/fees", data={"form": "fee", "type_name": "Aktiv",
                                            "amount": amount, "valid_from": "2026-07"})
    assert r.status_code == 422 and "Fehler" in r.text
    assert _count(db, "fee_rate") == 2


def test_fee_upper_limit_accepted(client, db):
    r = client.post("/settings/fees", data={"form": "fee", "type_name": "Aktiv",
                                            "amount": "100000,00", "valid_from": "2026-07"})
    assert r.status_code == 303 and fee_at(db, "Aktiv", "2026-07") == 10_000_000


def test_unhandled_error_is_safe_page(db_path):
    app = create_app(db_path)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("geheimer-fehlertext")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    assert "geheimer-fehlertext" not in r.text and "Traceback" not in r.text
    assert r.headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "same-origin"
