"""Host-Allowlist: testserver nur in Tests. Nur erfundene Daten."""
import pytest
from fastapi.testclient import TestClient

from kassivo.web.app import create_app
from tests.helpers import make_app


def _prod(tmp_path):
    return TestClient(create_app(tmp_path / "t.db"), follow_redirects=False)


def test_production_rejects_testserver(tmp_path):
    r = _prod(tmp_path).get("/dues", headers={"Host": "testserver"})
    assert r.status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8000", "localhost", "localhost:8000"])
def test_production_allows_local_hosts(tmp_path, host):
    r = _prod(tmp_path).get("/dues", headers={"Host": host})
    assert r.status_code == 200


def test_extra_hosts_honored(tmp_path):
    app = create_app(tmp_path / "t.db", extra_hosts={"kassivo.test"})
    c = TestClient(app, follow_redirects=False)
    assert c.get("/dues", headers={"Host": "kassivo.test"}).status_code == 200
    assert c.get("/dues", headers={"Host": "testserver"}).status_code == 400


def test_test_helper_allows_testserver(tmp_path):
    c = TestClient(make_app(tmp_path / "t.db"))
    assert c.get("/dues").status_code == 200


def test_tests_use_shared_helper():
    """Nur helpers.py und diese Datei rufen create_app direkt auf."""
    from pathlib import Path
    here = Path(__file__).parent
    offenders = [p.name for p in here.glob("test_*.py")
                 if p.name != Path(__file__).name and "create_app(" in p.read_text("utf-8")]
    assert offenders == []
