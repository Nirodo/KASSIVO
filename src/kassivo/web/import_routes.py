"""CAMT-Import per Upload. Der Dateiname des Clients wird nie verwendet."""
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile

from kassivo import categories, dues, matching
from kassivo.camt import CamtError
from kassivo.dues import check_start_is_set, distribution_counts, proposed_tx_count
from kassivo.ingest import import_path
from kassivo.web.common import _NOTES, get_conn, render

router = APIRouter()
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
UPLOAD_OVERHEAD_BYTES = 1024 * 1024  # Multipart-Rahmen kommt zur Dateigröße hinzu
CHUNK = 1024 * 1024
TOO_BIG = "Die Datei ist zu groß (Grenze 100 MB). Es wurde nichts importiert."
# Pfade mit Datei-Upload; für sie gilt die frühe Größenprüfung in der Middleware
UPLOAD_PATHS = frozenset({"/import", "/backup/upload"})


def upload_too_large(request) -> bool:
    """Früh ablehnen: Starlette spoolt den ganzen Multipart-Body auf die Platte, bevor der
    Handler läuft. Ohne (oder mit ungültigem) Content-Length greift die Grenze im Handler."""
    if request.method != "POST" or request.url.path not in UPLOAD_PATHS:
        return False
    raw = request.headers.get("content-length", "")
    if not re.fullmatch(r"[0-9]{1,15}", raw):  # alles andere: Grenze im Handler
        return False
    return int(raw) > MAX_UPLOAD_BYTES + UPLOAD_OVERHEAD_BYTES


def _sniff(head: bytes) -> str | None:
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    if head.lstrip().startswith(b"<"):
        return "xml"
    return None


def _form(request, conn, status=200, error=None):
    return render(request, "import.html", status, error=error,
                  check_start_set=check_start_is_set(conn))


@router.get("/import")
async def import_form(request: Request, conn=Depends(get_conn)):
    return _form(request, conn)


@router.post("/import")
async def import_submit(
    request: Request, file: UploadFile | None = File(None), conn=Depends(get_conn),
):
    if file is None:
        return _form(request, conn, 422, "Bitte eine Datei auswählen.")
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "upload.bin"
        total, head = 0, b""
        with raw.open("wb") as out:
            while chunk := await file.read(CHUNK):
                if total == 0:
                    head = chunk[:1024]
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    return _form(request, conn, 413, TOO_BIG)
                out.write(chunk)
        kind = _sniff(head)
        if kind is None:
            return _form(request, conn, 422, "Dateityp nicht erkannt. Erwartet wird eine ZIP- oder XML-Datei.")
        path = raw.rename(raw.with_name(f"upload.{kind}"))
        derived_before = None if check_start_is_set(conn) else dues.get_check_start(conn)
        try:
            result = import_path(conn, path)
        except CamtError as e:
            return _form(request, conn, 422, f"{e}. Es wurde nichts importiert.")
        categorized = categories.apply_rules(conn, result.new_tx_ids)
        assigned = matching.auto_assign(conn, result.new_tx_ids)
        redistribute_failed = False
        if not check_start_is_set(conn) and dues.get_check_start(conn) != derived_before:
            # abgeleiteter Prüfungsstart ist früher geworden: Abgeleitetes folgt.
            # Scheitert das, bleibt der Import gespeichert (eigener Commit davor).
            try:
                dues.redistribute_all(conn)
            except Exception:
                redistribute_failed = True
    auto_count, review_count, outside_count = distribution_counts(conn, result.new_tx_ids)
    return render(request, "import_result.html", result=result,
                  categorized=categorized, assigned=assigned,
                  auto_count=auto_count, review_count=review_count, outside_count=outside_count,
                  proposals=proposed_tx_count(conn), redistribute_failed=redistribute_failed,
                  redistribute_text=_NOTES["redistribute_failed"])
