"""Sicherheit Feinschliff: Sec-Fetch-Site, Konsolenprotokoll ohne Suchtexte, IBAN-Regeln. Nur erfundene Daten."""
import importlib
import io
import logging
import logging.config
import re
from pathlib import Path

import pytest

from kassivo import categories
from kassivo.db import connect
from tests.helpers import make_client

ROOT = Path(__file__).resolve().parent.parent
REFUSAL = "Anfrage von fremder Herkunft abgelehnt."

NEW_MEMBER = {"name": "Erika Muster", "email": "", "joined": "2024-01-01", "type": "single"}
ROUTES = [
    "/members/new", "/settings/fees", "/import", "/transactions/1/category",
    "/categories", "/categories/apply", "/backup",
]


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path / "t.db")


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("value", ["cross-site", "same-site", "bogus", "Cross-Site"])
def test_unsafe_refused_for_foreign_or_unknown_fetch_site(client, path, value):
    # Unbekannte nicht leere Werte werden abgelehnt (Verteidigung in der Tiefe).
    r = client.post(path, headers={"Sec-Fetch-Site": value})
    assert r.status_code == 403
    assert REFUSAL in r.text


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("value", ["same-origin", "none"])
def test_unsafe_passes_middleware_for_own_fetch_site(client, path, value):
    r = client.post(path, headers={"Sec-Fetch-Site": value})
    assert REFUSAL not in r.text


def test_member_create_not_refused_with_same_origin_or_without_header(client):
    r1 = client.post("/members/new", data=NEW_MEMBER, headers={"Sec-Fetch-Site": "same-origin"})
    r2 = client.post("/members/new", data=NEW_MEMBER)
    assert REFUSAL not in r1.text and REFUSAL not in r2.text


def test_empty_header_value_treated_as_absent(client):
    r = client.post("/members/new", data=NEW_MEMBER, headers={"Sec-Fetch-Site": ""})
    assert REFUSAL not in r.text


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_other_unsafe_methods_refused(client, method):
    r = client.request(method, "/members/new", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_origin_rules_independent(client):
    ok = {"Origin": "http://testserver"}
    evil = {"Origin": "http://evil.example"}
    post = client.post
    assert post("/members/new", data=NEW_MEMBER, headers=ok | {"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert post("/members/new", data=NEW_MEMBER, headers=evil | {"Sec-Fetch-Site": "same-origin"}).status_code == 403
    assert REFUSAL not in post("/members/new", data=NEW_MEMBER,
                               headers=ok | {"Sec-Fetch-Site": "same-origin"}).text


@pytest.mark.parametrize("value", ["cross-site", "same-site", "bogus"])
def test_get_never_affected(client, value):
    assert client.get("/dues", headers={"Sec-Fetch-Site": value}).status_code == 200


def test_refusal_carries_security_headers(client):
    r = client.post("/members/new", data=NEW_MEMBER, headers={"Sec-Fetch-Site": "cross-site"})
    assert "Content-Security-Policy" in r.headers
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "same-origin"


def test_host_check_runs_before_fetch_site(client):
    r = client.post("/members/new", data=NEW_MEMBER,
                    headers={"Host": "evil.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 400 and "Ungültiger Host." in r.text


def test_refused_member_post_changes_nothing(tmp_path):
    c = make_client(tmp_path / "t.db")
    r = c.post("/members/new", data=NEW_MEMBER, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and REFUSAL in r.text
    conn = connect(tmp_path / "t.db")
    assert conn.execute("select count(*) from member").fetchone()[0] == 0


def test_refused_import_changes_nothing(tmp_path):
    c = make_client(tmp_path / "t.db")
    r = c.post("/import", files={"file": ("a.xml", b"<x/>", "application/octet-stream")},
               headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and REFUSAL in r.text
    conn = connect(tmp_path / "t.db")
    assert conn.execute("select count(*) from import_batch").fetchone()[0] == 0


# --- Konsolenprotokoll ---

def _record(path):
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5000", "GET", path, "1.1", 200), None)


def test_filter_strips_query_keeps_path():
    from kassivo.logconfig import StripQueryFilter
    rec = _record("/transactions?q=Max+Mustermann&x=1")
    assert StripQueryFilter().filter(rec) is True
    assert rec.getMessage() == '127.0.0.1:5000 - "GET /transactions HTTP/1.1" 200'


def test_filter_leaves_plain_path_and_odd_records():
    from kassivo.logconfig import StripQueryFilter
    rec = _record("/dues")
    StripQueryFilter().filter(rec)
    assert "/dues HTTP" in rec.getMessage()
    odd = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "plain %s", ("x?y",), None)
    assert StripQueryFilter().filter(odd) is True
    assert odd.getMessage() == "plain x?y"


@pytest.fixture
def restore_uvicorn_loggers():
    names = ("uvicorn", "uvicorn.access", "uvicorn.error")
    saved = {n: (logging.getLogger(n).level, logging.getLogger(n).propagate,
                 list(logging.getLogger(n).handlers), list(logging.getLogger(n).filters))
             for n in names}
    yield
    for n, (level, prop, handlers, filters) in saved.items():
        lg = logging.getLogger(n)
        lg.setLevel(level)
        lg.propagate = prop
        lg.handlers[:] = handlers
        lg.filters[:] = filters


def test_log_config_filters_access_and_keeps_error_logger(restore_uvicorn_loggers):
    from kassivo.logconfig import build_log_config
    cfg = build_log_config()
    assert "uvicorn.error" in cfg["loggers"] and "uvicorn" in cfg["loggers"]
    assert cfg["handlers"]["access"]["filters"] == ["strip_query"]
    logging.config.dictConfig(cfg)
    h = logging.getLogger("uvicorn.access").handlers[0]
    buf = io.StringIO()
    h.setStream(buf)
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/transactions?q=Geheim", "1.1", 200)
    assert "Geheim" not in buf.getvalue() and "/transactions" in buf.getvalue()


def test_main_passes_log_config(tmp_path, monkeypatch):
    monkeypatch.setenv("KASSIVO_DB", str(tmp_path / "k.db"))
    main_mod = importlib.import_module("kassivo.__main__")
    calls = []
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda app, **kw: calls.append(kw))
    main_mod.main()
    kw = calls[0]
    assert kw["host"] == "127.0.0.1" and kw["port"] == 8000
    assert kw["log_config"]["handlers"]["access"]["filters"] == ["strip_query"]


def test_500_handler_still_generic(tmp_path):
    from fastapi.testclient import TestClient
    from tests.helpers import make_app
    app = make_app(tmp_path / "t.db")

    @app.get("/boom")
    async def boom():
        raise RuntimeError("Geheimer Name Max Mustermann")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500 and "Mustermann" not in r.text and "Interner Fehler" in r.text


# --- IBAN-Regel maskiert ---

def _rules_page(tmp_path, *texts):
    db = tmp_path / "t.db"
    c = make_client(db)
    c.get("/dues")
    conn = connect(db)
    cat = conn.execute("select id from ledger_account where number=3100").fetchone()[0]
    for t in texts:
        categories.add_rule(conn, cat, "cp_iban", t)
    conn.commit()
    conn.close()
    return c.get("/categories").text


def test_full_iban_rule_masked(tmp_path):
    html = _rules_page(tmp_path, "DE00 0000 0000 0000 0000 00", "de00000000000000000000")
    assert "DE00…0000" in html and "de00…0000" in html
    assert "DE00 0000 0000 0000 0000 00" not in html and "de00000000000000000000" not in html


def test_plain_and_short_rules_unchanged(tmp_path):
    html = _rules_page(tmp_path, "Spende Verein", "DE12 3456", "AB12")
    assert "„Spende Verein“" in html and "„DE12 3456“" in html and "„AB12“" in html


def test_rule_text_still_escaped(tmp_path):
    html = _rules_page(tmp_path, "<b>x</b>")
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html


@pytest.mark.parametrize("text,expected", [
    ("DE89370400440532013000", "DE89…3000"),
    ("DE89 3704 0044 0532 0130 00", "DE89…3000"),
    ("de89370400440532013000", "de89…3000"),
    ("DE89\u00a03704\u00a00044\u00a00532\u00a00130\u00a000", "DE89…3000"),
    ("DE89-3704-0044-0532-0130-00", "DE89…3000"),  # Bindestriche wie Leerzeichen
    ("GB29NWBK60161331926819", "GB29…6819"),
    ("DE89370400440532013000 Max Mustermann", None),
    ("DE89370400440532013000 Max", None),
    ("IBAN DE89 3704 0044 0532 0130 00", None),  # Vorwort: nicht maskiert
    ("RE2024000012345", None),
    ("AB12CDEFGHIJKL", None),
    ("DE8937040044053201300", None),  # zu kurz
    ("DE893704004405320130000", None),  # zu lang
    ("DE89A70400440532013000", None),  # Buchstabe nach Prüfziffern bei DE
    ("Spende Verein", None),
    ("", None),
])
def test_mask_rule_text(text, expected):
    from kassivo.web.common import mask_rule_text
    assert mask_rule_text(text) == (text if expected is None else expected)


def test_mask_rule_text_non_string_unchanged():
    from kassivo.web.common import mask_rule_text
    assert mask_rule_text(None) is None


def test_stored_rule_value_unchanged(tmp_path):
    _rules_page(tmp_path, "DE00000000000000000000")
    conn = connect(tmp_path / "t.db")
    assert conn.execute("select contains from posting_rule").fetchone()[0] == "DE00000000000000000000"


# --- Doku ---

def test_docs_mention_new_protections():
    wiki = (ROOT / "src/kassivo/web/templates/wiki/sicherheit.html").read_text("utf-8")
    readme = (ROOT / "README.md").read_text("utf-8")
    for text in (wiki, readme):
        assert "Sec-Fetch-Site" in text
        assert "ohne Suchtexte" in text
        assert "Besteht eine Regel nur aus einer vollständigen IBAN" in text
        assert re.search(r"Regeln?.{0,80}gekürzt", text)
        assert "einschließlich unbekannter Werte" in text
