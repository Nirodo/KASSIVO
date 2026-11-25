"""Parser für Sparkasse-Exporte im Format camt.052.001.08 (ein Konto, ein Buchungstag)."""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

NS = "urn:iso:std:iso:20022:tech:xsd:camt.052.001.08"
_N = {"c": NS}
MAX_EUR = 10**9  # obere Grenze je Betrag; schützt vor riesigen Exponenten
E_AMOUNT = "ungültiger Betrag"
_AMOUNT_RE = re.compile(r"[0-9]+(\.[0-9]{1,2})?")


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
    skipped_zero: int = 0  # übersprungene Einträge mit 0,00 €
    skipped_date: date | None = None  # spätestes Buchungsdatum davon


def _text(el, path: str) -> str | None:
    found = el.find(path, _N) if el is not None else None
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _cents(text: str | None, sign_ind: str | None) -> int:
    """XML-Dezimalzahl (Punkt) in Cent, ohne float. DBIT negativ.

    Der Text ist ohne Vorzeichen (das Vorzeichen steht in CdtDbtInd) und liegt
    in 0 <= Betrag <= 10**9 EUR; 0 (höchstens zwei Nachkommastellen) liefert 0,
    der Aufrufer überspringt solche Einträge. Alles außer ASCII-Ziffern mit optionalem
    Punkt und höchstens zwei Nachkommastellen (NaN, Exponent, Vorzeichen) wird zu CamtError mit fester Meldung (nie der Eingabetext).
    """
    if text is None or sign_ind not in ("CRDT", "DBIT"):
        raise CamtError("Betrag oder Soll/Haben-Kennzeichen fehlt")
    text = text.strip()
    if not _AMOUNT_RE.fullmatch(text):  # xs:decimal ohne Vorzeichen, Exponent, Sonderziffern
        raise CamtError(E_AMOUNT)
    cents = int(Decimal(text) * 100)
    if cents == 0:
        return 0  # Aufrufer überspringt Einträge mit 0,00
    if cents > MAX_EUR * 100:
        raise CamtError(E_AMOUNT)
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
    # Sammelbuchung (mehrere TxDtls): keine Gegenpartei, damit sie nie dem
    # ersten Zahler automatisch zugeordnet wird.
    only = txs[0] if len(txs) == 1 else None
    side, acct = ("Dbtr", "DbtrAcct") if ind == "CRDT" else ("Cdtr", "CdtrAcct")
    base = f"c:RltdPties/c:{side}"
    name = _text(only, f"{base}/c:Pty/c:Nm") or _text(only, f"{base}/c:Nm")
    iban = _text(only, f"c:RltdPties/c:{acct}/c:Id/c:IBAN")
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
    rpts = root.findall("c:BkToCstmrAcctRpt/c:Rpt", _N)
    if not rpts:
        raise CamtError("Rpt fehlt")
    if len(rpts) > 1:
        raise CamtError("Mehrere Berichte in einer Datei werden nicht unterstützt.")
    rpt = rpts[0]
    iban = _text(rpt, "c:Acct/c:Id/c:IBAN")
    if iban is None:
        raise CamtError("IBAN fehlt")
    parsed = [
        _entry(n) for n in rpt.findall("c:Ntry", _N) if _text(n, "c:Sts/c:Cd") == "BOOK"
    ]
    entries = [t for t in parsed if t.amount_cents != 0]
    zeros = [t.booking_date for t in parsed if t.amount_cents == 0]
    return ParsedStatement(
        iban, _balance(rpt, "OPBD"), _balance(rpt, "CLBD"), entries,
        len(zeros), max(zeros) if zeros else None,
    )
