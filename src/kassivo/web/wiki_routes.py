"""Wiki im Programm: feste Hilfeseiten, nur lesend. Slugs gelten nur laut Tabelle."""
from fastapi import APIRouter, HTTPException, Request

from kassivo.web.common import render

router = APIRouter()

GROUPS = (
    "Einstieg",
    "Mitglieder und Beiträge",
    "Bank und Zahlungen",
    "Auswertung",
    "Betrieb und Sicherheit",
    "Hilfe",
)

# slug -> (Titel, Vorlage, Kurzbeschreibung, Gruppe). Die Reihenfolge ist die der Seitenleiste.
# Vorlagennamen stehen hier fest; aus der Anfrage wird nie ein Pfad gebaut.
TOPICS: dict[str, tuple[str, str, str, str]] = {
    "start": ("Erste Schritte", "wiki/start.html",
              "Empfohlener Ablauf vom ersten Start bis zum Report.", "Einstieg"),
    "begriffe": ("Begriffe", "wiki/begriffe.html",
                 "Glossar: was Mitglied, Verteilung, Vorschlag und Co. bedeuten.", "Einstieg"),
    "mitglieder": ("Mitglieder", "wiki/mitglieder.html",
                   "Anlegen, ändern, Mitgliedsart wechseln, Eintritt, Austritt und IBAN.",
                   "Mitglieder und Beiträge"),
    "beitraege": ("Beiträge und Prüfungsstart", "wiki/beitraege.html",
                  "Beitragshöhe je Art, Gültig-ab-Monat und der Prüfungsstart.",
                  "Mitglieder und Beiträge"),
    "import": ("Kontoexport importieren", "wiki/import.html",
               "Export aus dem Online-Banking hochladen, Duplikate, Warnungen, Fehlermeldungen.",
               "Bank und Zahlungen"),
    "zuordnung": ("Zahlungen zuordnen", "wiki/zuordnung.html",
                  "Automatische und manuelle Zuordnung, „Unklar“, Absender merken, Geschwister.",
                  "Bank und Zahlungen"),
    "verteilung": ("Verteilung auf Beitragsmonate", "wiki/verteilung.html",
                   "Wann KASSIVO selbst bestätigt, Vorschläge prüfen, ändern und aufheben.",
                   "Bank und Zahlungen"),
    "kategorien": ("Kategorien und Regeln", "wiki/kategorien.html",
                   "Kategorien anlegen, Regeln mit Priorität, Regeln anwenden, von Hand setzen.",
                   "Bank und Zahlungen"),
    "beitragsuebersicht": ("Beitragsübersicht", "wiki/beitragsuebersicht.html",
                           "Das Raster Mitglieder mal Monate, die Status und die Zahlungshistorie.",
                           "Auswertung"),
    "prognose": ("Prognose", "wiki/prognose.html",
                 "Was an Mitgliedsbeiträgen bis zu einem Stichtag noch offen ist.", "Auswertung"),
    "reports": ("Jahresreport", "wiki/reports.html",
                "Einnahmen, Ausgaben und Saldo eines Jahres, Diagramme und Tabellen.",
                "Auswertung"),
    "backup": ("Sicherung und Wiederherstellung", "wiki/backup.html",
               "Sicherung erstellen und von Hand zurückspielen.", "Betrieb und Sicherheit"),
    "sicherheit": ("Datenschutz und Sicherheit", "wiki/sicherheit.html",
                   "Was geschützt ist, was nicht, und was Sie selbst beachten sollten.",
                   "Betrieb und Sicherheit"),
    "faq": ("Fehlerbehebung (FAQ)", "wiki/faq.html",
            "Häufige Probleme mit Ursache und Lösung.", "Hilfe"),
    "grenzen": ("Bekannte Einschränkungen", "wiki/grenzen.html",
                "Was KASSIVO 1.0.0 nicht kann.", "Hilfe"),
}


def _context(slug: str | None) -> dict:
    keys = list(TOPICS)
    i = keys.index(slug) if slug in TOPICS else None
    return {
        "groups": [(g, [(s, t[0]) for s, t in TOPICS.items() if t[3] == g]) for g in GROUPS],
        "topics": TOPICS,
        "slug": slug,
        "prev": None if not i else (keys[i - 1], TOPICS[keys[i - 1]][0]),
        "next": None if i is None or i + 1 >= len(keys) else (keys[i + 1], TOPICS[keys[i + 1]][0]),
    }


@router.get("/wiki")
async def wiki_index(request: Request):
    return render(request, "wiki/index.html", **_context(None))


@router.get("/wiki/{slug}")
async def wiki_topic(request: Request, slug: str):
    topic = TOPICS.get(slug)
    if topic is None:
        raise HTTPException(404)
    return render(request, topic[1], wiki_title=topic[0], **_context(slug))
