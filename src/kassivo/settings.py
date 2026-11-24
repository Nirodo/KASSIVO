"""Ort der Datenbank.

Standard ist ein lokaler Ordner außerhalb des Projekts, damit Namen, IBANs und
Sicherungen nicht mit einem Cloud-Ordner (z. B. OneDrive) synchronisiert werden.
Verworfen: relativer Pfad data/kassivo.db (liegt im Projektordner und legt beim
Start aus einem anderen Ordner still eine leere Datenbank an).
"""
import os
import sys
from pathlib import Path

CLOUD_MARKERS = ("onedrive", "dropbox", "google drive", "icloud")


def default_db_path(env=None, platform: str | None = None, home=None) -> Path:
    """KASSIVO_DB gewinnt (absolut aufgelöst), sonst der lokale App-Datenordner."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    override = env.get("KASSIVO_DB")
    if override:
        return Path(override).expanduser().resolve()
    if platform == "win32":
        local = env.get("LOCALAPPDATA")
        base = Path(local) if local else home / "AppData" / "Local"
        return base / "KASSIVO" / "kassivo.db"
    xdg = env.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".local" / "share"
    return base / "kassivo" / "kassivo.db"


def backup_dir(db_path: Path) -> Path:
    return Path(db_path).parent / "backups"


def cloud_warning(db_path: Path) -> str | None:
    parts = [p.casefold() for p in Path(db_path).parts]
    if any(m in p for p in parts for m in CLOUD_MARKERS):
        return ("Warnung: Die Datenbank liegt in einem Cloud-Ordner (z. B. OneDrive). "
                "Namen, IBANs und Sicherungen werden dann in die Cloud synchronisiert. "
                "Besser einen lokalen Pfad über KASSIVO_DB wählen.")
    return None


def startup_lines(db_path: Path) -> list[str]:
    lines = [f"Datenbank: {db_path}", f"Sicherungen: {backup_dir(db_path)}"]
    warning = cloud_warning(db_path)
    if warning:
        lines.append(warning)
    return lines
