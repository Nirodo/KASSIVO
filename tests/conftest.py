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
def conn4():
    """Datenbank im Speicher auf Schema 4 (Migration 4 ausdrücklich, bis sie eingetragen ist)."""
    from kassivo import db
    c = connect(":memory:", migrations={**db.MIGRATIONS, 4: db._migrate_to_4})
    yield c
    c.close()


@pytest.fixture
def ledger_conn():
    """Schema-3-Datenbank im Speicher plus Buchungstabellen von Schema 4 (Task 3, vor der
    Migration). Startplan, Perioden ab dem frühesten Umsatzjahr bis 2026."""
    from datetime import date

    from kassivo import db
    c = connect(":memory:")
    for stmt in db._statements(db._LEDGER_DDL_4):
        c.execute(stmt)
    db._seed_ledger_4(c, date(2026, 11, 28))
    c.commit()
    yield c
    c.close()
