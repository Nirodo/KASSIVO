from datetime import date

from kassivo.months import add_months, month_end, month_of, month_range


def test_month_of():
    assert month_of(date(2026, 11, 24)) == "2026-11"


def test_add_months():
    assert add_months("2026-12", 1) == "2027-01"
    assert add_months("2026-01", -1) == "2025-12"


def test_month_range():
    assert month_range("2026-11", "2027-01") == ["2026-11", "2026-12", "2027-01"]
    assert month_range("2027-01", "2026-11") == []


def test_month_end():
    assert month_end("2026-02") == date(2026, 2, 28)
    assert month_end("2028-02") == date(2028, 2, 29)


def test_add_months_year_bounds():
    import pytest
    with pytest.raises(ValueError):
        add_months("9999-12", 1)
    with pytest.raises(ValueError):
        add_months("0001-01", -1)
    assert add_months("9999-11", 1) == "9999-12"


def test_month_range_terminates_at_max_year():
    assert month_range("9999-11", "9999-12") == ["9999-11", "9999-12"]
    assert month_range("2026-12", "2026-11") == []
