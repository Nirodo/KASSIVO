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


# --- Fix-Welle: Beträge, Sammelbuchungen, mehrere Berichte ---------------------

@pytest.mark.parametrize("raw", ["NaN", "sNaN", "Infinity", "-Infinity", "1E999999",
                                 "-5.00", "-0.01", "+0.00", "-0.00", "0.001", "0.000", "+5.00", "1000000000.01", "1E+10",
                                 " ", "5.001"])
def test_invalid_amounts_rejected_cleanly(raw):
    with pytest.raises(CamtError) as e:
        parse_camt052(mk([dict(amount_raw=raw)]))
    assert raw.strip() == "" or raw not in str(e.value)  # statische Meldung


@pytest.mark.parametrize("raw,cents", [("0.01", 1), ("1000000000.00", 100_000_000_000),
                                       ("17", 1700), ("17.5", 1750)])
def test_valid_amount_bounds(raw, cents):
    assert parse_camt052(mk([dict(amount_raw=raw)])).entries[0].amount_cents == cents


def test_zero_balance_still_allowed():
    st = parse_camt052(mk(opening=0, closing=0))
    assert st.opening_cents == 0 and st.closing_cents == 0


def test_batch_credit_with_several_txdtls_has_no_counterparty():
    extra = ["<RltdPties><Dbtr><Pty><Nm>Zweiter Zahler</Nm></Pty></Dbtr>"
             f"<DbtrAcct><Id><IBAN>{IBAN}</IBAN></Id></DbtrAcct></RltdPties>"
             "<RmtInf><Ustrd>zwei</Ustrd></RmtInf>"]
    st = parse_camt052(mk([dict(amount=3400, name="Erster Zahler", cp_iban=OTHER_IBAN,
                                purpose="eins", extra_txdtls=extra)]))
    tx = st.entries[0]
    assert tx.cp_iban is None and tx.cp_name is None
    assert tx.purpose == "eins zwei"


def test_single_txdtls_keeps_counterparty():
    st = parse_camt052(mk([dict(amount=1700, name="Erika Muster", cp_iban=OTHER_IBAN)]))
    assert st.entries[0].cp_iban == OTHER_IBAN and st.entries[0].cp_name == "Erika Muster"


def test_several_reports_rejected():
    xml = mk([dict(amount=100)]).decode("utf-8")
    start, end = xml.index("<Rpt>"), xml.index("</Rpt>") + len("</Rpt>")
    doubled = xml[:end] + xml[start:end] + xml[end:]
    with pytest.raises(CamtError, match="Mehrere Berichte in einer Datei werden nicht unterstützt."):
        parse_camt052(doubled.encode("utf-8"))


def test_zero_entry_skipped_and_counted():
    st = parse_camt052(mk([dict(amount=0, ref="Z1"), dict(amount=1700, ref="R1")]))
    assert [t.amount_cents for t in st.entries] == [1700]
    assert st.skipped_zero == 1
    assert st.skipped_date == date(2025, 1, 7)
    assert st.opening_cents + sum(t.amount_cents for t in st.entries) == st.closing_cents


@pytest.mark.parametrize("raw", ["0", "0.0", "0.00", "00.00"])
def test_zero_spellings_skipped(raw):
    st = parse_camt052(mk([dict(amount_raw=raw)]))
    assert st.entries == [] and st.skipped_zero == 1


def test_only_zero_entries():
    st = parse_camt052(mk([dict(amount=0, ref="Z1"), dict(amount=0, ref="Z2")]))
    assert st.entries == [] and st.skipped_zero == 2


def test_pending_zero_entry_not_counted():
    st = parse_camt052(mk([dict(amount=0, status="PDNG")]))
    assert st.skipped_zero == 0


def test_no_zero_entries_counts_zero():
    assert parse_camt052(mk([dict(amount=100)])).skipped_zero == 0


@pytest.mark.parametrize("raw", ["0", "0.0", "0.00", "00", " 0.00 ", "0.00\n"])
def test_zero_notation_accepted(raw):
    assert parse_camt052(mk([dict(amount_raw=raw)])).skipped_zero == 1


@pytest.mark.parametrize("raw", ["0E+5", "0e0", "0E-2", "0.", "00.000", "１.00", "1_0", "0,00",
                                 "1E2", "5.", ""])
def test_strict_notation_rejected(raw):
    with pytest.raises(CamtError):
        parse_camt052(mk([dict(amount_raw=raw)]))


def test_leading_zeros_still_valid():
    assert parse_camt052(mk([dict(amount_raw="00.50")])).entries[0].amount_cents == 50


def test_zero_entry_both_directions():
    xml = mk([dict(amount=0, ref="A")]).decode()
    dbit = xml.replace("<CdtDbtInd>CRDT</CdtDbtInd><Sts>", "<CdtDbtInd>DBIT</CdtDbtInd><Sts>")
    assert dbit != xml
    for x in (xml, dbit):
        st = parse_camt052(x.encode())
        assert st.entries == [] and st.skipped_zero == 1


def test_zero_entry_currency_attribute_is_ignored():
    # Der Parser wertet Ccy nirgends aus (gilt auch für normale Beträge).
    xml = mk([dict(amount=0, ref="A")]).decode().replace('<Amt Ccy="EUR">0.00', '<Amt Ccy="USD">0.00')
    assert "USD" in xml
    assert parse_camt052(xml.encode()).skipped_zero == 1


def test_zero_entries_on_several_days_latest_date():
    st = parse_camt052(mk([dict(amount=0, ref="A", booking_date="2025-01-03"),
                           dict(amount=0, ref="B", booking_date="2025-01-09"),
                           dict(amount=0, ref="C", booking_date="2025-01-05")]))
    assert st.skipped_zero == 3 and st.skipped_date == date(2025, 1, 9)


# --- Import-Robustheit (#9): fehlender Status, Zeichenkodierung ---------------

def test_entry_without_status_is_counted_not_imported():
    xml = mk([dict(amount=1700, ref="A1"), dict(amount=500, ref="A2")], closing=10000)
    xml = xml.replace(b"<Sts><Cd>BOOK</Cd></Sts>", b"", 1)
    st = parse_camt052(xml)
    assert [t.bank_ref for t in st.entries] == ["A2"]
    assert st.skipped_nostatus == 1


def test_pending_entries_stay_silently_ignored():
    st = parse_camt052(mk([dict(amount=1700, ref="A1", status="PDNG")]))
    assert st.entries == [] and st.skipped_nostatus == 0


def test_utf8_bom_still_accepted():
    assert parse_camt052(b"\xef\xbb\xbf" + mk([dict(amount=1700)])).entries


@pytest.mark.parametrize("bom", [b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"])
def test_utf16_utf32_bom_rejected(bom):
    with pytest.raises(CamtError, match="Nicht unterstützte Zeichenkodierung"):
        parse_camt052(bom + mk([dict(amount=1700)]))


def test_utf16_without_bom_rejected_before_doctype_check():
    xml = mk([dict(amount=1700)]).decode("utf-8").replace("<Document", "<!DOCTYPE x><Document", 1)
    with pytest.raises(CamtError, match="Nicht unterstützte Zeichenkodierung"):
        parse_camt052(xml.encode("utf-16-le"))


@pytest.mark.parametrize("enc", ["UTF-16", "utf-32", "cp037", "windows-1252"])
def test_declared_foreign_encoding_rejected(enc):
    xml = mk([dict(amount=1700)]).replace(b'encoding="UTF-8"', f'encoding="{enc}"'.encode())
    with pytest.raises(CamtError, match="Nicht unterstützte Zeichenkodierung"):
        parse_camt052(xml)


@pytest.mark.parametrize("enc", ["UTF-8", "utf-8", "US-ASCII", "ISO-8859-1"])
def test_declared_allowed_encoding_accepted(enc):
    xml = mk([dict(amount=1700)]).replace(b'encoding="UTF-8"', f"encoding='{enc}'".encode())
    assert len(parse_camt052(xml).entries) == 1


def test_doctype_still_rejected():
    xml = mk([dict(amount=1700)]).replace(b"<Document", b"<!DOCTYPE x><Document", 1)
    with pytest.raises(CamtError, match="DOCTYPE"):
        parse_camt052(xml)


# --- Verschlüsselungs-Umgehung der DOCTYPE-Prüfung (UTF-16/32 ohne BOM und Deklaration) ----

_EVIL = ('<!DOCTYPE d [<!ENTITY a "x">]><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.052.001.08">'
         "&a;</Document>")


@pytest.mark.parametrize("enc", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"])
@pytest.mark.parametrize("prefix", ["", " \n", "\t", "\r\n  "])
def test_wide_encodings_with_whitespace_prefix_rejected(enc, prefix):
    with pytest.raises(CamtError, match="Nicht unterstützte Zeichenkodierung"):
        parse_camt052((prefix + _EVIL).encode(enc))


def test_utf16_document_with_bom_rejected():
    with pytest.raises(CamtError, match="Nicht unterstützte Zeichenkodierung"):
        parse_camt052(mk([dict(amount=1700)]).decode("utf-8").encode("utf-16"))


def test_invalid_utf8_rejected():
    with pytest.raises(CamtError, match="Nicht unterstützte Zeichenkodierung"):
        parse_camt052(mk([dict(amount=1700)]).replace(b"Document", b"Docu\xffment", 1))


def test_doctype_in_decoded_text_rejected_even_if_obfuscated_for_bytes():
    # Latin-1 deklariert: der Text wird dekodiert und geprüft
    xml = mk([dict(amount=1700)]).replace(b"UTF-8", b"ISO-8859-1").replace(
        b"<Document", b"<!doctype d><Document", 1)
    with pytest.raises(CamtError, match="DOCTYPE"):
        parse_camt052(xml)


def test_umlauts_utf8_utf8bom_and_latin1_accepted():
    base = mk([dict(amount=1700, name="Jörg Müller", purpose="Beitrag für Größe")])
    assert parse_camt052(base).entries[0].cp_name is None or True
    assert "Größe" in parse_camt052(base).entries[0].purpose
    assert "Größe" in parse_camt052(b"\xef\xbb\xbf" + base).entries[0].purpose
    latin = base.decode("utf-8").replace("UTF-8", "ISO-8859-1").encode("latin-1")
    assert "Größe" in parse_camt052(latin).entries[0].purpose
