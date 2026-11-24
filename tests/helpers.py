"""Hilfen für Tests: erfundene CAMT-052-Dateien. Nur erfundene Daten."""
import io
import zipfile
from xml.sax.saxutils import escape

NS = "urn:iso:std:iso:20022:tech:xsd:camt.052.001.08"
IBAN = "DE00000000000000000000"
OTHER_IBAN = "DE11111111111111111111"


def entry(amount="17.00", ind="CRDT", name=None, iban=None, purpose=None,
          booking="2025-01-07", value="2025-01-07", ref="REF1", status="BOOK",
          extra_txdtls=()):
    """Ein Ntry. name/iban sind die Gegenpartei; purpose str oder Liste."""
    parties = ""
    if name or iban:
        if ind == "CRDT":
            tag, acct = "Dbtr", "DbtrAcct"
            other = ("Cdtr", "CdtrAcct")
        else:
            tag, acct = "Cdtr", "CdtrAcct"
            other = ("Dbtr", "DbtrAcct")
        nm = f"<Nm>{escape(name)}</Nm>" if name else ""
        ib = f"<{acct}><Id><IBAN>{iban}</IBAN></Id></{acct}>" if iban else ""
        parties = (
            f"<RltdPties><{tag}><Pty>{nm}</Pty></{tag}>{ib}"
            f"<{other[0]}><Pty><Nm>Eigenes Konto</Nm></Pty></{other[0]}>"
            f"<{other[1]}><Id><IBAN>{IBAN}</IBAN></Id></{other[1]}>"
            f"</RltdPties>"
        )
    if purpose is None:
        purposes = []
    elif isinstance(purpose, str):
        purposes = [purpose]
    else:
        purposes = list(purpose)
    rmt = ""
    if purposes:
        rmt = "<RmtInf>" + "".join(f"<Ustrd>{escape(p)}</Ustrd>" for p in purposes) + "</RmtInf>"
    refx = f"<AcctSvcrRef>{ref}</AcctSvcrRef>" if ref else ""
    valx = f"<ValDt><Dt>{value}</Dt></ValDt>" if value else ""
    extra = "".join(f"<TxDtls>{x}</TxDtls>" for x in extra_txdtls)
    return (
        f'<Ntry><Amt Ccy="EUR">{amount}</Amt><CdtDbtInd>{ind}</CdtDbtInd>'
        f"<Sts><Cd>{status}</Cd></Sts><BookgDt><Dt>{booking}</Dt></BookgDt>"
        f"{valx}{refx}<NtryDtls><TxDtls>{parties}{rmt}</TxDtls>{extra}</NtryDtls></Ntry>"
    )


def bal(code, amount, ind, day="2025-01-07"):
    return (
        f"<Bal><Tp><CdOrPrtry><Cd>{code}</Cd></CdOrPrtry></Tp>"
        f'<Amt Ccy="EUR">{amount}</Amt><CdtDbtInd>{ind}</CdtDbtInd>'
        f"<Dt><Dt>{day}</Dt></Dt></Bal>"
    )


def make_camt(entries=(), opening=("100.00", "CRDT"), closing=("100.00", "CRDT"),
              iban=IBAN, ns=NS) -> bytes:
    bals = ""
    if opening:
        bals += bal("OPBD", *opening)
    if closing:
        bals += bal("CLBD", *closing)
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?><Document xmlns="{ns}">'
        f"<BkToCstmrAcctRpt><Rpt><Acct><Id><IBAN>{iban}</IBAN></Id><Ccy>EUR</Ccy></Acct>"
        f"{bals}{''.join(entries)}</Rpt></BkToCstmrAcctRpt></Document>"
    )
    return xml.encode("utf-8")


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return buf.getvalue()
