from datetime import date

import pytest

from kassivo.camt import CamtError, parse_camt052
from tests.helpers import IBAN, OTHER_IBAN, entry, make_camt


def test_incoming():
    st = parse_camt052(make_camt([entry("17.00", "CRDT", "Max Mustermann", OTHER_IBAN, "Beitrag November")]))
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
    st = parse_camt052(make_camt([entry("50.00", "DBIT", "Erika Beispiel", OTHER_IBAN, "Miete")]))
    tx = st.entries[0]
    assert tx.amount_cents == -5000
    assert tx.cp_name == "Erika Beispiel"
    assert tx.cp_iban == OTHER_IBAN


def test_fee_without_parties():
    st = parse_camt052(make_camt([entry("1.5", "DBIT")]))
    tx = st.entries[0]
    assert tx.amount_cents == -150
    assert tx.cp_name is None and tx.cp_iban is None
    assert tx.purpose == ""


def test_umlaut_name():
    st = parse_camt052(make_camt([entry(name="Jürgen Müller", purpose="Beitrag")]))
    assert st.entries[0].cp_name == "Jürgen Müller"


def test_multiple_ustrd_and_txdtls_joined():
    extra = ["<RmtInf><Ustrd>drei</Ustrd></RmtInf>"]
    st = parse_camt052(make_camt([entry(purpose=["eins", "zwei"], extra_txdtls=extra)]))
    assert len(st.entries) == 1
    assert st.entries[0].purpose == "eins zwei drei"


def test_balances():
    st = parse_camt052(make_camt(opening=("100.00", "CRDT"), closing=("12.34", "DBIT")))
    assert st.opening_cents == 10000
    assert st.closing_cents == -1234


def test_missing_balances_none():
    st = parse_camt052(make_camt(opening=None, closing=None))
    assert st.opening_cents is None and st.closing_cents is None


def test_only_booked():
    st = parse_camt052(make_camt([entry(status="PDNG"), entry(ref="B")]))
    assert [t.bank_ref for t in st.entries] == ["B"]


def test_missing_value_date_and_ref():
    st = parse_camt052(make_camt([entry(value=None, ref=None)]))
    assert st.entries[0].value_date is None
    assert st.entries[0].bank_ref is None


def test_broken_xml():
    with pytest.raises(CamtError):
        parse_camt052(b"<kaputt")


def test_other_schema():
    with pytest.raises(CamtError):
        parse_camt052(make_camt(ns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"))


def test_doctype_rejected():
    bad = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><Document/>'
    with pytest.raises(CamtError):
        parse_camt052(bad)


def test_bad_amount():
    with pytest.raises(CamtError):
        parse_camt052(make_camt([entry("abc")]))
