"""CAMT-Import per Upload. Der Dateiname des Clients wird nie verwendet."""
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile

from kassivo import categories, matching
from kassivo.camt import CamtError
from kassivo.ingest import import_path
from kassivo.web.common import get_conn, render

router = APIRouter()
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
CHUNK = 1024 * 1024


def _sniff(head: bytes) -> str | None:
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    if head.lstrip().startswith(b"<"):
        return "xml"
    return None


def _form(request, status=200, error=None):
    return render(request, "import.html", status, error=error)


@router.get("/import")
async def import_form(request: Request):
    return _form(request)


@router.post("/import")
async def import_submit(
    request: Request, file: UploadFile | None = File(None), conn=Depends(get_conn),
):
    if file is None:
        return _form(request, 422, "Bitte eine Datei auswählen.")
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "upload.bin"
        total, head = 0, b""
        with raw.open("wb") as out:
            while chunk := await file.read(CHUNK):
                if total == 0:
                    head = chunk[:1024]
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    return _form(request, 413, "Die Datei ist zu groß (Grenze 100 MB). Es wurde nichts importiert.")
                out.write(chunk)
        kind = _sniff(head)
        if kind is None:
            return _form(request, 422, "Dateityp nicht erkannt. Erwartet wird eine ZIP- oder XML-Datei.")
        path = raw.rename(raw.with_name(f"upload.{kind}"))
        try:
            result = import_path(conn, path)
        except CamtError as e:
            return _form(request, 422, f"{e}. Es wurde nichts importiert.")
        categorized = categories.apply_rules(conn, result.new_tx_ids)
        assigned = matching.auto_assign(conn, result.new_tx_ids)
    return render(request, "import_result.html", result=result,
                  categorized=categorized, assigned=assigned)
