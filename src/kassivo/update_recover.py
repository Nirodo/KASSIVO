"""Journal und Wiederherstellung für den Austausch der Programmdateien (#31).

Diese Datei nutzt nur die Standardbibliothek und importiert nichts aus kassivo. apply_update
kopiert sie vor dem Austausch nach <Projekt>/.update/wiederherstellen.py. So lässt sich ein
abgebrochener Austausch auch dann zurücksetzen, wenn src/kassivo gerade halb ausgetauscht und
nicht importierbar ist:

    .venv\\Scripts\\python.exe -I .update\\wiederherstellen.py .

update.bat ruft genau das auf, sobald .update/journal.json existiert.

Journal: vor dem ersten Verschieben steht der ganze Plan in journal.json (mit fsync). Jeder
Eintrag ist (Pfad, alt_vorhanden). Ob ein Schritt schon gelaufen ist, zeigt das Dateisystem:
liegt die alte Datei im alt-Ordner, wurde sie verschoben; fehlt die neue im neu-Ordner, wurde
sie eingesetzt. Deshalb ist die Wiederherstellung wiederholbar und braucht kein Protokoll je
Schritt.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

JOURNAL = "journal.json"
WORK = ".update"
RETRIES = 6
RETRY_DELAY = 0.5  # Sekunden, verdoppelt sich je Versuch (OneDrive, Virenscanner)


def move(src: Path, dst: Path, *, sleep=time.sleep) -> None:
    """os.replace mit Wiederholung bei PermissionError (gesperrte Datei, Cloud-Synchronisation)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == RETRIES - 1:
                raise
            sleep(RETRY_DELAY * 2 ** attempt)


def write_journal(work: Path, data: dict) -> None:
    tmp = work / (JOURNAL + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, work / JOURNAL)


def read_journal(project: Path) -> dict | None:
    path = Path(project) / WORK / JOURNAL
    if not path.is_file():
        return None
    return json.loads(path.read_text("utf-8"))


def _has_files(folder: Path) -> bool:
    return folder.is_dir() and any(p.is_file() for p in folder.rglob("*"))


def recover(project, *, run=subprocess.run, python=None, sleep=time.sleep) -> list[str]:
    """Setzt einen begonnenen Austausch laut Journal zurück. Gibt Meldungen zurück."""
    project = Path(project)
    work = project / WORK
    j = read_journal(project)
    if j is None:
        return []
    alt, neu, fehl = work / j["alt"], work / j["neu"], work / j["fehl"]
    for rel, had_old in reversed(j["files"]):
        cur, old, new = project / rel, alt / rel, neu / rel
        if had_old:
            if old.exists():
                if cur.exists():
                    move(cur, fehl / rel, sleep=sleep)
                move(old, cur, sleep=sleep)
        elif cur.exists() and not new.exists():
            move(cur, fehl / rel, sleep=sleep)
    notes = [f"Programmdateien auf {j['current']} zurückgesetzt."]
    if j.get("pip_ran") and j.get("freeze"):
        freeze = work / j["freeze"]
        py = python or sys.executable
        r = run([py, "-m", "pip", "install", "-r", str(freeze)], cwd=project,
                capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            notes.append("Python-Pakete auf den alten Stand zurückgesetzt.")
        else:
            notes.append("Python-Pakete konnten nicht zurückgesetzt werden. Reparatur:"
                         f" .venv\\Scripts\\python -m pip install -r {WORK}\\{j['freeze']}")
    (work / JOURNAL).unlink()
    for folder in (neu, fehl):
        shutil.rmtree(folder, ignore_errors=True)  # nur eigene Ordner, Inhalt ist Paketinhalt
    if not _has_files(alt):
        shutil.rmtree(alt, ignore_errors=True)
    return notes


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    project = Path(argv[0] if argv else ".")
    try:
        notes = recover(project)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Wiederherstellung fehlgeschlagen ({type(exc).__name__}). Bitte im Wiki unter"
              " „Update“ die Schritte für die Wiederherstellung von Hand ausführen.")
        return 2
    if not notes:
        print("Kein unvollständiges Update gefunden.")
        return 0
    for n in notes:
        print(n)
    print("Der unvollständige Austausch wurde zurückgesetzt. KASSIVO kann wieder gestartet werden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
