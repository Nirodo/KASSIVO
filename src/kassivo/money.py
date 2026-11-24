import re
from decimal import Decimal

_AMOUNT = re.compile(r"-?(\d+|\d{1,3}(\.\d{3})+)(,\d{1,2})?", re.ASCII)  # \d nur 0-9


def parse_cents(text: str) -> int:
    """Deutsches Betragsformat ("1.234,5") in Cent. Ungültig: ValueError."""
    s = text.strip()
    if not _AMOUNT.fullmatch(s):
        raise ValueError(f"ungültiger Betrag: {text!r}")
    return int(Decimal(s.replace(".", "").replace(",", ".")) * 100)


def format_eur(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    euros, rest = divmod(abs(cents), 100)
    return f"{sign}{euros:,}".replace(",", ".") + f",{rest:02d} €"
