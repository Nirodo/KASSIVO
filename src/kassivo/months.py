import calendar
from datetime import date


def _split(m: str) -> tuple[int, int]:
    y, mo = m.split("-")
    return int(y), int(mo)


def month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def add_months(m: str, n: int) -> str:
    y, mo = _split(m)
    y, mo = divmod(y * 12 + mo - 1 + n, 12)
    return f"{y:04d}-{mo + 1:02d}"


def month_range(a: str, b: str) -> list[str]:
    """Alle Monate von a bis b, inklusive. Leer, wenn a nach b liegt."""
    out = []
    while a <= b:
        out.append(a)
        a = add_months(a, 1)
    return out


def month_end(m: str) -> date:
    y, mo = _split(m)
    return date(y, mo, calendar.monthrange(y, mo)[1])
