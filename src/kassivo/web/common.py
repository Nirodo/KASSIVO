"""Gemeinsame Helfer der Web-Schicht: Templates, DB pro Anfrage, Eingabeprüfung."""
import re
from datetime import date
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from kassivo.db import connect
from kassivo.ingest import mask_iban
from kassivo.money import format_eur
from kassivo.months import month_of, valid_month

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["eur"] = format_eur
MONTH_ABBR = ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")
templates.env.filters["month_abbr"] = lambda n: MONTH_ABBR[n - 1]

# Gesamtlänge der IBAN je Land (häufige Länder). Unbekannte Länder werden nie gekürzt.
_IBAN_LENGTH = {"AT": 20, "BE": 16, "CH": 21, "DE": 22, "DK": 18, "ES": 24, "FI": 18, "FR": 27,
                "GB": 22, "IE": 22, "IT": 27, "LU": 20, "NL": 18, "NO": 15, "PL": 28, "PT": 25,
                "SE": 24}
_IBAN_CHARS_RE = re.compile(r"[A-Za-z0-9 \u00a0-]+", re.ASCII)
_IBAN_COMPACT_RE = re.compile(r"[A-Za-z]{2}[0-9]{2}[A-Za-z0-9]+", re.ASCII)


def mask_rule_text(text):
    """Regeltext für die Liste: besteht er nur aus einer vollständigen IBAN, nur gekürzt zeigen.

    Leerzeichen, geschützte Leerzeichen und Bindestriche zwischen Gruppen sind erlaubt.
    Ein Vorwort ("IBAN DE89 ...") oder Nachwort ("... Max") verhindert das Kürzen, ebenso
    ein unbekanntes Land oder eine falsche Länge. DE-IBANs haben nach dem Länderkürzel nur Ziffern.
    Gespeichert wird weiter der Originaltext."""
    if not isinstance(text, str) or not _IBAN_CHARS_RE.fullmatch(text):
        return text
    compact = re.sub(r"[ \u00a0-]", "", text)
    if not _IBAN_COMPACT_RE.fullmatch(compact):
        return text
    country = compact[:2].upper()
    if _IBAN_LENGTH.get(country) != len(compact):
        return text
    if country == "DE" and not compact[2:].isdigit():
        return text
    return mask_iban(compact)


templates.env.filters["rule_text"] = mask_rule_text

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


_NOTES = {
    "auto": "{n} unklare Zahlungen automatisch zugeordnet.",
    "learned": "Zugeordnet. {n} weitere Zahlungen mit derselben Absender-IBAN wurden"
               " automatisch zugeordnet.",
    "ambiguous": "Zugeordnet. Die Absender-IBAN gehört mehreren Mitgliedern und wird"
                 " künftig nicht automatisch zugeordnet.",
    "changed": "Gemerkt: Dieser Absender gehört jetzt zum gewählten Mitglied (vorher zu"
               " einem anderen). Zahlt er für mehrere Mitglieder, legen Sie das unter"
               " „Gemerkte Zuordnungen“ fest. Weitere offene Zahlungen ordnet „Unklare"
               " automatisch zuordnen“ zu.",
    "changed_r": "Gemerkt: Dieser Absender gehört jetzt zum gewählten Mitglied (vorher zu"
                 " einem anderen). Zahlt er für mehrere Mitglieder, legen Sie das unter"
                 " „Gemerkte Zuordnungen“ fest. Weitere offene Zahlungen ordnet „Unklare"
                 " automatisch zuordnen“ zu. {n} weitere Zahlungen neu verteilt.",
    "cleaned": "Gelöschte Sicherungen: {n}.",
    "restored": "Sicherung eingespielt: {n} Mitglieder, {m} Buchungen.",
    "cleaned_partial": "Gelöschte Sicherungen: {n}. {m} konnten nicht gelöscht werden.",
    "reproposed": "Prüfungsstart gespeichert. {n} Zahlungen neu verteilt.",
    "undone": "Automatische Verteilung rückgängig gemacht. Die Zahlung steht jetzt unter"
              " „Offene Vorschläge“ und kann auf der Seite „Verteilung“ geändert und"
              " bestätigt werden.",
    "recomputed": "{n} Zahlungen neu verteilt.",
    "recomputed_other": "{n} weitere Zahlungen neu verteilt.",
    "learned_r": "Zugeordnet. {n} weitere Zahlungen mit derselben Absender-IBAN wurden"
                 " automatisch zugeordnet. {m} weitere Zahlungen neu verteilt.",
    "ambiguous_r": "Zugeordnet. Die Absender-IBAN gehört mehreren Mitgliedern und wird"
                   " künftig nicht automatisch zugeordnet. {n} weitere Zahlungen neu verteilt.",
    "undone_r": "Automatische Verteilung rückgängig gemacht. Die Zahlung steht jetzt unter"
                " „Offene Vorschläge“ und kann auf der Seite „Verteilung“ geändert und"
                " bestätigt werden. {m} weitere Zahlungen neu verteilt.",
    "redistribute_failed": "Zahlungen konnten nicht neu verteilt werden: bitte Prüfungsstart"
                           " erneut speichern.",
    "run": "{n} geprüft: {a} automatisch verteilt, {r} bleiben zur Prüfung.",
    "run_outside": "{n} geprüft: {a} automatisch verteilt, {r} bleiben zur Prüfung,"
                   " {o} liegen vor dem Prüfungsstart.",
    "reproposed_outside": "Prüfungsstart gespeichert. {n} Zahlungen neu verteilt,"
                          " {o} liegen vor dem Prüfungsstart.",
}
# Zahlen, die ein Hinweis zusätzlich zu n verwenden darf (nur ganze Zahlen)
_NOTE_KEYS = ("a", "r", "o")
_COUNT_RE = re.compile(r"[0-9]{1,7}", re.ASCII)


def page_note(request: Request) -> str | None:
    """Hinweis nach einer Aktion. Nur feste Codes und Zahlen, nie freier Text."""
    template = _NOTES.get(request.query_params.get("note", ""))
    if template is None:
        return None
    values = {}
    for key in ("n", "m") + _NOTE_KEYS:
        raw = request.query_params.get(key, "0" if key == "m" else None)
        if raw is None:
            if "{" + key + "}" in template:
                return None
            continue
        if not _COUNT_RE.fullmatch(raw):
            return None
        values[key] = int(raw)
    return template.format(**values)


def with_note(url: str, code: str, n: int, m: int | None = None, **extra: int) -> str:
    assert code in _NOTES and set(extra) <= set(_NOTE_KEYS)
    tail = "" if m is None else f"&m={int(m)}"
    tail += "".join(f"&{k}={int(extra[k])}" for k in _NOTE_KEYS if k in extra)
    return f"{url}{'&' if '?' in url else '?'}note={code}&n={int(n)}{tail}"


templates.env.globals["page_note"] = page_note

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
