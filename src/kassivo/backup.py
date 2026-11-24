"""Sicherung der Datenbank: konsistente Kopie über die SQLite-Backup-API."""
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

NAME_RE = re.compile(r"kassivo-(\d{8})-(\d{6})(?:-(\d+))?\.db")
MAX_LISTED = 50


def _copy(conn: sqlite3.Connection, target: Path) -> None:
    src, own = conn, None
    if conn.in_transaction:
        # Offene Änderungen gehören nicht in die Sicherung, und ein Backup aus der
        # Verbindung mit offener Schreibtransaktion würde hängen: frische Verbindung.
        files = [r[2] for r in conn.execute("pragma database_list") if r[1] == "main"]
        if not files or not files[0]:
            raise sqlite3.OperationalError("Sicherung nur bei dateibasierter Datenbank möglich.")
        src = own = sqlite3.connect(files[0])
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        if own is not None:
            own.close()


def backup_db(conn: sqlite3.Connection, dest_dir: Path, now: datetime | None = None) -> Path:
    """Schreibt kassivo-JJJJMMTT-HHMMSS.db in dest_dir (nie überschreiben, atomar)."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = "kassivo-" + (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    final, n = dest_dir / f"{stem}.db", 1
    while final.exists():
        n += 1
        final = dest_dir / f"{stem}-{n}.db"
    tmp = dest_dir / f".{final.name}.tmp"
    try:
        _copy(conn, tmp)
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return final


def format_size(n: int) -> str:
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB".replace(".", ",")
    return f"{n / 1024 / 1024:.1f} MB".replace(".", ",")


def list_backups(dest_dir: Path | None) -> list[dict]:
    """Bis zu 50 Sicherungen, neueste zuerst. Nur Dateien mit passendem Namen."""
    if dest_dir is None or not Path(dest_dir).is_dir():
        return []
    found = []
    for p in Path(dest_dir).iterdir():
        m = NAME_RE.fullmatch(p.name)
        if m and p.is_file():
            found.append(((m[1], m[2], int(m[3] or 1)), p, m))
    found.sort(key=lambda t: t[0], reverse=True)
    out = []
    for _, p, m in found[:MAX_LISTED]:
        d, t = m[1], m[2]
        out.append({
            "name": p.name, "size": format_size(p.stat().st_size),
            "stamp": f"{d[6:]}.{d[4:6]}.{d[:4]} {t[:2]}:{t[2:4]}:{t[4:]}",
        })
    return out
