import pytest

from kassivo.fees import fee_at, set_fee


def test_default_fees(conn):
    assert fee_at(conn, "Aktiv", "2026-11") == 1700
    assert fee_at(conn, "Passiv", "2026-11") == 1000


def test_fee_change_not_retroactive(conn):
    set_fee(conn, "Aktiv", 2000, "2027-01")
    assert fee_at(conn, "Aktiv", "2026-12") == 1700
    assert fee_at(conn, "Aktiv", "2027-01") == 2000


def test_unknown_type(conn):
    with pytest.raises(ValueError):
        fee_at(conn, "Ehren", "2026-11")
