from datetime import date

import pytest

from kassivo.camt import CamtError, parse_camt052
from tests.helpers import IBAN, OTHER_IBAN, make_camt

D = "2025-01-07"


def mk(entries=(), opening=10000, **kw):
    return make_camt(IBAN, opening, list(entries), D, **kw)


def test_incoming():
    st = parse_camt052(mk([dict(amount=1700, name="Max Mustermann", cp_iban=OTHER_IBAN, purpose="Beitrag November")]))
    tx = st.entries[0]
    assert tx.amount_cents == 1700
    assert tx.cp_name == "Max Mustermann"
    assert tx.cp_iban == OTHER_IBAN
    assert tx.purpose == "Beitrag November"
    assert tx.booking_date == date(2025, 1, 7)
    assert tx.value_date == date(2025, 1, 7)
    assert tx.bank_ref == "REF1"
    assert st.account_iban == IBAN


def test_outgoing_uses_creditor():
    st = parse_camt052(mk([dict(amount=-5000, name="Erika Beispiel", cp_iban=OTHER_IBAN, purpose="Miete")]))
    tx = st.entries[0]
    assert tx.amount_cents == -5000
    assert tx.cp_name == "Erika Beispiel"
    assert tx.cp_iban == OTHER_IBAN


def test_fee_without_parties():
    st = parse_camt052(mk([dict(amount=-150)]))
    tx = st.entries[0]
    assert tx.amount_cents == -150
    assert tx.cp_name is None and tx.cp_iban is None
    assert tx.purpose == ""


def test_umlaut_name():
    st = parse_camt052(mk([dict(amount=100, name="Jürgen Müller", purpose="Beitrag")]))
    assert st.entries[0].cp_name == "Jürgen Müller"


def test_multiple_ustrd_and_txdtls_joined():
    extra = ["<RmtInf><Ustrd>drei</Ustrd></RmtInf>"]
    st = parse_camt052(mk([dict(amount=100, purpose=["eins", "zwei"], extra_txdtls=extra)]))
    assert len(st.entries) == 1
    assert st.entries[0].purpose == "eins zwei drei"


def test_balances_wrong_closing_kept_as_given():
    st = parse_camt052(mk(opening=10000, closing=-1234))
    assert st.opening_cents == 10000
    assert st.closing_cents == -1234


def test_derived_closing():
    st = parse_camt052(mk([dict(amount=1700), dict(amount=-5000, ref="R2")], opening=10000))
    assert st.opening_cents == 10000
    assert st.closing_cents == 6700
    assert st.opening_cents + sum(t.amount_cents for t in st.entries) == st.closing_cents


def test_derived_closing_negative():
    st = parse_camt052(mk([dict(amount=-5000)], opening=1000))
    assert st.closing_cents == -4000


def test_missing_balances_none():
    st = parse_camt052(mk(with_balances=False))
    assert st.opening_cents is None and st.closing_cents is None


def test_only_booked():
    st = parse_camt052(mk([dict(amount=100, status="PDNG"), dict(amount=100, ref="B")]))
    assert [t.bank_ref for t in st.entries] == ["B"]


def test_missing_value_date_and_ref():
    st = parse_camt052(mk([dict(amount=100, value_date=None, ref=None)]))
    assert st.entries[0].value_date is None
    assert st.entries[0].bank_ref is None


def test_entry_booking_date_override():
    st = parse_camt052(mk([dict(amount=100, booking_date="2025-01-03")]))
    assert st.entries[0].booking_date == date(2025, 1, 3)


def test_broken_xml():
    with pytest.raises(CamtError):
        parse_camt052(b"<kaputt")


def test_other_schema():
    with pytest.raises(CamtError):
        parse_camt052(mk(ns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"))


def test_doctype_rejected():
    bad = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><Document/>'
    with pytest.raises(CamtError):
        parse_camt052(bad)


def test_bad_amount():
    with pytest.raises(CamtError):
        parse_camt052(mk([dict(amount_raw="abc")]))
