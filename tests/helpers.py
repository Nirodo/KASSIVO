"""Hilfen für Tests: erfundene CAMT-052-Dateien. Nur erfundene Daten."""
import io
import zipfile
from xml.sax.saxutils import escape

NS = "urn:iso:std:iso:20022:tech:xsd:camt.052.001.08"
IBAN = "DE00000000000000000000"
OTHER_IBAN = "DE11111111111111111111"


def _fmt(cents: int) -> str:
    return f"{abs(cents) // 100}.{abs(cents) % 100:02d}"


def _entry(e: dict, day: str) -> str:
    """Ein Ntry aus einem Dict. Pflicht: amount (signierte Cent)."""
    cents = e.get("amount", 0)
    ind = "DBIT" if cents < 0 else "CRDT"
    amount = e["amount_raw"] if "amount_raw" in e else _fmt(cents)
    name, iban, purpose = e.get("name"), e.get("cp_iban"), e.get("purpose")
    parties = ""
    if name or iban:
        tag, acct, other = (
            ("Dbtr", "DbtrAcct", ("Cdtr", "CdtrAcct")) if ind == "CRDT"
            else ("Cdtr", "CdtrAcct", ("Dbtr", "DbtrAcct"))
        )
        nm = f"<Nm>{escape(name)}</Nm>" if name else ""
        ib = f"<{acct}><Id><IBAN>{iban}</IBAN></Id></{acct}>" if iban else ""
        parties = (
            f"<RltdPties><{tag}><Pty>{nm}</Pty></{tag}>{ib}"
            f"<{other[0]}><Pty><Nm>Eigenes Konto</Nm></Pty></{other[0]}>"
            f"<{other[1]}><Id><IBAN>{IBAN}</IBAN></Id></{other[1]}></RltdPties>"
        )
    purposes = [] if purpose is None else ([purpose] if isinstance(purpose, str) else list(purpose))
    rmt = ""
    if purposes:
        rmt = "<RmtInf>" + "".join(f"<Ustrd>{escape(p)}</Ustrd>" for p in purposes) + "</RmtInf>"
    ref = e.get("ref", "REF1")
    refx = f"<AcctSvcrRef>{ref}</AcctSvcrRef>" if ref else ""
    value = e.get("value_date", day)
    valx = f"<ValDt><Dt>{value}</Dt></ValDt>" if value else ""
    extra = "".join(f"<TxDtls>{x}</TxDtls>" for x in e.get("extra_txdtls", ()))
    return (
        f'<Ntry><Amt Ccy="EUR">{amount}</Amt><CdtDbtInd>{ind}</CdtDbtInd>'
        f"<Sts><Cd>{e.get('status', 'BOOK')}</Cd></Sts>"
        f"<BookgDt><Dt>{e.get('booking_date', day)}</Dt></BookgDt>"
        f"{valx}{refx}<NtryDtls><TxDtls>{parties}{rmt}</TxDtls>{extra}</NtryDtls></Ntry>"
    )


def _bal(code: str, cents: int, day: str) -> str:
    ind = "DBIT" if cents < 0 else "CRDT"
    return (
        f"<Bal><Tp><CdOrPrtry><Cd>{code}</Cd></CdOrPrtry></Tp>"
        f'<Amt Ccy="EUR">{_fmt(cents)}</Amt><CdtDbtInd>{ind}</CdtDbtInd>'
        f"<Dt><Dt>{day}</Dt></Dt></Bal>"
    )


def make_camt(iban: str, opening: int, entries: list[dict], day: str, *,
              closing: int | None = None, ns: str = NS,
              with_balances: bool = True) -> bytes:
    """Erfundene camt.052. Schluss-Saldo = opening + Summe der Beträge,
    außer `closing` ist gesetzt. Nur gebuchte Einträge zählen zur Summe."""
    if closing is None:
        closing = opening + sum(
            e.get("amount", 0) for e in entries if e.get("status", "BOOK") == "BOOK"
        )
    bals = _bal("OPBD", opening, day) + _bal("CLBD", closing, day) if with_balances else ""
    body = "".join(_entry(e, day) for e in entries)
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?><Document xmlns="{ns}">'
        f"<BkToCstmrAcctRpt><Rpt><Acct><Id><IBAN>{iban}</IBAN></Id><Ccy>EUR</Ccy></Acct>"
        f"{bals}{body}</Rpt></BkToCstmrAcctRpt></Document>"
    )
    return xml.encode("utf-8")


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return buf.getvalue()
