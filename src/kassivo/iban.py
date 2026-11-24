def normalize_iban(iban: str | None) -> str:
    """Ohne jeden Leerraum, Großbuchstaben. None oder leer ergibt ''."""
    return "".join((iban or "").split()).upper()
