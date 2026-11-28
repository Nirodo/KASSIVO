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

Das Journal ist eine Datei im Projektordner und damit nicht vertrauenswürdig: jeder Wert wird
geprüft, bevor irgendetwas verschoben, gelöscht oder an pip gegeben wird (validate_journal).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

JOURNAL = "journal.json"
WORK = ".update"
RETRIES = 6
RETRY_DELAY = 0.5  # Sekunden, verdoppelt sich je Versuch (OneDrive, Virenscanner)
TAG = r"[0-9]{8}-[0-9]{6}-[0-9a-f]{4}"
_DIR_RE = {"alt": re.compile(rf"a-{TAG}"), "neu": re.compile(rf"n-{TAG}"),
           "fehl": re.compile(rf"f-{TAG}")}
_FREEZE_RE = re.compile(rf"pip-{TAG}\.txt")
# Nie anfassen, auch wenn ein Journal oder eine Dateiliste es verlangt
FORBIDDEN_TOP = frozenset({".git", ".venv", ".update", ".env"})
E_INVALID = ("Das Journal des Updates enthält unzulässige Werte. Es wurde nichts geändert."
             " Bitte im Wiki unter „Update“ die Wiederherstellung von Hand ausführen.")


class JournalError(ValueError):
    """Journal oder Dateiliste enthält unzulässige Werte; es wurde nichts geändert."""


def safe_rel(project, rel) -> bool:
    """Relativer Pfad einer Programmdatei: kein .., nicht absolut, kein Laufwerk, kein
    geschützter Ordner, und aufgelöst (auch über Links) innerhalb des Projektordners."""
    if not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel or rel.startswith("/"):
        return False
    if any(ord(c) < 32 or ord(c) == 127 for c in rel):
        return False
    parts = rel.split("/")
    # Windows-Aliasse: Punkt/Leerzeichen am Ende werden gestrichen (".git." ist ".git"),
    # "~" kann ein 8.3-Kurzname sein ("GIT~1")
    if any(part in ("", ".", "..") or part.endswith((".", " ")) or "~" in part
           for part in parts):
        return False
    if parts[0].casefold() in FORBIDDEN_TOP:
        return False
    root = Path(project).resolve()
    target = (root / rel).resolve()
    # noch einmal am aufgelösten Pfad: ein Link darf nicht in einen geschützten Ordner zeigen
    return root in target.parents and target.relative_to(root).parts[0].casefold() not in FORBIDDEN_TOP


def validate_journal(project, j) -> None:
    """Prüft jeden Wert, bevor irgendetwas verschoben oder gelöscht wird."""
    if not isinstance(j, dict) or not isinstance(j.get("current"), str):
        raise JournalError(E_INVALID)
    for key, pattern in _DIR_RE.items():
        if not isinstance(j.get(key), str) or not pattern.fullmatch(j[key]):
            raise JournalError(E_INVALID)
    freeze = j.get("freeze")
    if freeze is not None and (not isinstance(freeze, str) or not _FREEZE_RE.fullmatch(freeze)):
        raise JournalError(E_INVALID)
    files = j.get("files")
    if not isinstance(files, list):
        raise JournalError(E_INVALID)
    for entry in files:
        if (not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[1], bool)
                or not safe_rel(project, entry[0])):
            raise JournalError(E_INVALID)


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


def read_journal(project) -> dict | None:
    """Rohes Journal (ungeprüft) oder None. recover prüft es vor jeder Änderung."""
    path = Path(project) / WORK / JOURNAL
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise JournalError(E_INVALID) from None


def _has_files(folder: Path) -> bool:
    return folder.is_dir() and any(p.is_file() for p in folder.rglob("*"))


def recover(project, *, run=subprocess.run, python=None, sleep=time.sleep) -> list[str]:
    """Setzt einen begonnenen Austausch laut Journal zurück. Gibt Meldungen zurück.

    Wirft JournalError ohne jede Änderung, wenn das Journal unzulässige Werte enthält."""
    project = Path(project)
    work = project / WORK
    j = read_journal(project)
    if j is None:
        return []
    validate_journal(project, j)
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
    if j.get("pip_ran") is True and j.get("freeze"):
        repair = (" Reparatur: .venv\\Scripts\\python -m pip install -r"
                  f" {WORK}\\{j['freeze']}")
        py = python or sys.executable
        try:
            r = run([py, "-m", "pip", "install", "-r", str(work / j["freeze"])], cwd=project,
                    capture_output=True, text=True, timeout=600)
            ok = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            notes.append("Python-Pakete aus der vorher gesicherten Liste neu installiert"
                         " (pip install -r). Zusätzlich installierte Pakete bleiben.")
        else:
            notes.append("Python-Pakete konnten nicht zurückgesetzt werden." + repair)
    (work / JOURNAL).unlink()
    for folder in (neu, fehl):
        shutil.rmtree(folder, ignore_errors=True)  # geprüfte eigene Ordner, Inhalt ist Paketinhalt
    if not _has_files(alt):
        shutil.rmtree(alt, ignore_errors=True)
    return notes


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    project = Path(argv[0] if argv else ".")
    try:
        notes = recover(project)
    except JournalError as exc:
        print(str(exc))
        return 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Wiederherstellung fehlgeschlagen ({type(exc).__name__}). Bitte im Wiki unter"
              " „Update“ die Schritte für die Wiederherstellung von Hand ausführen.")
        return 2
    except KeyboardInterrupt:
        print("Abgebrochen (Strg+C). Bitte update.bat erneut starten, die Wiederherstellung"
              " setzt dort fort.")
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
