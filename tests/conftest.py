import pytest

from kassivo.db import connect


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _kein_netz(monkeypatch):
    """Tests machen nie echte Netzaufrufe: der echte HTTP-Client der Update-Prüfung ist gesperrt."""
    from kassivo import update

    def blocked():
        raise RuntimeError("Netzaufruf in Tests gesperrt")
    monkeypatch.setattr(update, "_build_opener", blocked)


@pytest.fixture
def ledger_conn(monkeypatch):
    """Frische Datenbank im Speicher (Schema 4) mit festem Stichtag 2026-11-28: Startplan,
    Periode "Jahr 2026"."""
    from datetime import date

    from kassivo import db
    monkeypatch.setattr(db, "_today", lambda: date(2026, 11, 28))
    c = connect(":memory:")
    yield c
    c.close()
