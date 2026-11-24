"""Parser für Sparkasse-Exporte im Format camt.052.001.08 (ein Konto, ein Buchungstag)."""
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

NS = "urn:iso:std:iso:20022:tech:xsd:camt.052.001.08"
_N = {"c": NS}


class CamtError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedTx:
    booking_date: date
    value_date: date | None
    amount_cents: int
    bank_ref: str | None
    cp_name: str | None
    cp_iban: str | None
    purpose: str


@dataclass
class ParsedStatement:
    account_iban: str
    opening_cents: int | None
    closing_cents: int | None
    entries: list[ParsedTx]


def _text(el, path: str) -> str | None:
    found = el.find(path, _N) if el is not None else None
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _cents(text: str | None, sign_ind: str | None) -> int:
    """XML-Dezimalzahl (Punkt) in Cent, ohne float. DBIT negativ."""
    if text is None or sign_ind not in ("CRDT", "DBIT"):
        raise CamtError("Betrag oder Soll/Haben-Kennzeichen fehlt")
    try:
        d = Decimal(text.strip())
    except InvalidOperation:
        raise CamtError("ungültiger Betrag") from None
    cents = d * 100
    if not d.is_finite() or cents != cents.to_integral_value():
        raise CamtError("ungültiger Betrag")
    cents = int(cents)
    return -cents if sign_ind == "DBIT" else cents


def _date(text: str | None) -> date | None:
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        raise CamtError("ungültiges Datum") from None


def _balance(rpt, code: str) -> int | None:
    for bal in rpt.findall("c:Bal", _N):
        if _text(bal, "c:Tp/c:CdOrPrtry/c:Cd") == code:
            return _cents(_text(bal, "c:Amt"), _text(bal, "c:CdtDbtInd"))
    return None


def _entry(ntry) -> ParsedTx:
    ind = _text(ntry, "c:CdtDbtInd")
    booking = _date(_text(ntry, "c:BookgDt/c:Dt"))
    if booking is None:
        raise CamtError("Buchungsdatum fehlt")
    txs = ntry.findall("c:NtryDtls/c:TxDtls", _N)
    first = txs[0] if txs else None
    side, acct = ("Dbtr", "DbtrAcct") if ind == "CRDT" else ("Cdtr", "CdtrAcct")
    base = f"c:RltdPties/c:{side}"
    name = _text(first, f"{base}/c:Pty/c:Nm") or _text(first, f"{base}/c:Nm")
    iban = _text(first, f"c:RltdPties/c:{acct}/c:Id/c:IBAN")
    parts = [
        u.text.strip()
        for tx in txs
        for u in tx.findall("c:RmtInf/c:Ustrd", _N)
        if u.text and u.text.strip()
    ]
    return ParsedTx(
        booking_date=booking,
        value_date=_date(_text(ntry, "c:ValDt/c:Dt")),
        amount_cents=_cents(_text(ntry, "c:Amt"), ind),
        bank_ref=_text(ntry, "c:AcctSvcrRef"),
        cp_name=name,
        cp_iban=iban,
        purpose=" ".join(parts),
    )


def parse_camt052(xml: bytes) -> ParsedStatement:
    head = xml.upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise CamtError("DOCTYPE/ENTITY nicht erlaubt")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise CamtError(f"kein gültiges XML: {e}") from None
    if root.tag != f"{{{NS}}}Document":
        raise CamtError("kein camt.052.001.08-Dokument")
    rpt = root.find("c:BkToCstmrAcctRpt/c:Rpt", _N)
    if rpt is None:
        raise CamtError("Rpt fehlt")
    iban = _text(rpt, "c:Acct/c:Id/c:IBAN")
    if iban is None:
        raise CamtError("IBAN fehlt")
    entries = [
        _entry(n) for n in rpt.findall("c:Ntry", _N) if _text(n, "c:Sts/c:Cd") == "BOOK"
    ]
    return ParsedStatement(iban, _balance(rpt, "OPBD"), _balance(rpt, "CLBD"), entries)
