"""Gemeinsame Helfer der Web-Schicht: Templates, DB pro Anfrage, Eingabeprüfung."""
import re
from datetime import date
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from kassivo.db import connect
from kassivo.money import format_eur
from kassivo.months import month_of, valid_month

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["eur"] = format_eur
MONTH_ABBR = ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")
templates.env.filters["month_abbr"] = lambda n: MONTH_ABBR[n - 1]

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
MIN_YEAR, MAX_YEAR = 1900, 2100
E_DATE = "Bitte ein gültiges Datum im Format JJJJ-MM-TT angeben (Jahr 1900 bis 2100)."
E_YEAR = f"Bitte ein gültiges Jahr zwischen {MIN_YEAR} und {MAX_YEAR} angeben."
_YEAR_RE = re.compile(r"[0-9]{4}", re.ASCII)
E_MONTH = "Bitte einen gültigen Monat im Format JJJJ-MM angeben."


async def get_conn(request: Request):
    """Eine Verbindung pro Anfrage. Async, damit alles im selben Thread läuft."""
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def render(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx, status_code=status)


async def get_today() -> date:
    """Heutiges Datum als Dependency, damit Tests es per dependency_overrides festlegen."""
    return date.today()


def current_month() -> str:
    return month_of(date.today())


def parse_date(text: str) -> date | None:
    """ISO-Datum JJJJ-MM-TT, Jahr 1900..2100. Sonst None."""
    text = text.strip()
    if not _DATE_RE.fullmatch(text):
        return None
    try:
        d = date.fromisoformat(text)
    except ValueError:
        return None
    return d if MIN_YEAR <= d.year <= MAX_YEAR else None


def parse_month(text: str) -> str | None:
    text = text.strip()
    if valid_month(text) and MIN_YEAR <= int(text[:4]) <= MAX_YEAR:
        return text
    return None


def type_names(conn) -> list[str]:
    return [r["name"] for r in conn.execute("select name from membership_type order by id")]


MAX_ID = 2**63 - 1
MAX_CENTS = 10_000_000
_NEXT_RE = re.compile(r"/(transactions|unmatched)([/?][!-~]*)?", re.ASCII)


def check_id(value: int) -> int:
    """EINE Prüfung für alle ID-Pfade: außerhalb 1..2**63-1 gibt es kein DB-Zugriff."""
    if not 1 <= value <= MAX_ID:
        raise HTTPException(404)
    return value


def parse_id(text: str) -> int | None:
    """Ziffernfolge im int64-Bereich, sonst None."""
    text = text.strip()
    if not re.fullmatch(r"[0-9]{1,19}", text):
        return None
    value = int(text)
    return value if 1 <= value <= MAX_ID else None


def safe_next(text: str | None, default: str = "/transactions") -> str:
    """Nur lokale Pfade unter /transactions oder /unmatched, nie ein Schema oder //."""
    if text and _NEXT_RE.fullmatch(text) and "\\" not in text:
        return text
    return default


def parse_year(text: str | None, today: date) -> int | None:
    """Fehlt der Parameter: laufendes Jahr. Ungültig: None."""
    if text is None:
        return today.year
    text = text.strip()
    if _YEAR_RE.fullmatch(text) and MIN_YEAR <= int(text) <= MAX_YEAR:
        return int(text)
    return None


def year_nav(year: int) -> dict:
    return {"prev": year - 1 if year > MIN_YEAR else None,
            "next": year + 1 if year < MAX_YEAR else None}


def year_error(request: Request, back: str) -> HTMLResponse:
    return render(request, "error.html", 422, message=E_YEAR, back=back, back_label="Zurück")
