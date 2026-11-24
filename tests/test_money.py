import pytest

from kassivo.money import format_eur, parse_cents


def test_parse_cents():
    assert parse_cents("17,00") == 1700
    assert parse_cents("1.234,5") == 123450
    assert parse_cents("-10,50") == -1050


def test_parse_cents_invalid():
    for bad in ("abc", "", "1,234", "1,2,3"):
        with pytest.raises(ValueError):
            parse_cents(bad)


def test_format_eur():
    assert format_eur(1700) == "17,00 €"
    assert format_eur(-1050) == "-10,50 €"
    assert format_eur(123456) == "1.234,56 €"
