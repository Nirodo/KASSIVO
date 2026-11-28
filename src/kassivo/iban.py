def normalize_iban(iban: str | None) -> str:
    """Ohne jeden Leerraum, Großbuchstaben. None oder leer ergibt ''."""
    return "".join((iban or "").split()).upper()


def mask_iban(iban: str) -> str:
    """Erste vier und letzte vier Zeichen; kürzere Werte ganz verdeckt."""
    return f"{iban[:4]}…{iban[-4:]}" if len(iban) > 8 else "…"
