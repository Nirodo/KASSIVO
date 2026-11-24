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
TOPICS: dict[str, tuple[str, str, str, str]] = {}


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
