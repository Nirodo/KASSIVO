"""Sicherung der Datenbank: konsistente Kopie über die SQLite-Backup-API."""
import os
import re
import stat
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

NAME_RE = re.compile(r"kassivo-(\d{8})-(\d{6})(?:-(\d+))?\.db", re.ASCII)
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


def _candidates(dest_dir: Path, stem: str):
    yield dest_dir / f"{stem}.db"
    n = 2
    while True:
        yield dest_dir / f"{stem}-{n}.db"
        n += 1


def _reserve(dest_dir: Path, stem: str) -> Path:
    """Legt den Zielnamen exklusiv an (xb), nimmt bei Kollision den nächsten Suffix."""
    for final in _candidates(dest_dir, stem):
        try:
            with open(final, "xb"):
                return final
        except FileExistsError:
            continue


def _fsync_file(path: Path) -> None:
    with open(path, "r+b") as f:
        os.fsync(f.fileno())


def _fsync_dir(path: Path) -> None:
    """Verzeichniseintrag sichern, wo das System es kann (Windows: nicht möglich)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def backup_db(conn: sqlite3.Connection, dest_dir: Path, now: datetime | None = None) -> Path:
    """Schreibt kassivo-JJJJMMTT-HHMMSS.db in dest_dir (nie überschreiben, atomar, dauerhaft)."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = "kassivo-" + (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    tmp = dest_dir / f".{stem}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    final = None
    try:
        final = _reserve(dest_dir, stem)
        _copy(conn, tmp)
        _fsync_file(tmp)
        os.replace(tmp, final)
        _fsync_dir(dest_dir)
    except BaseException:
        tmp.unlink(missing_ok=True)
        if final is not None:
            final.unlink(missing_ok=True)  # nur unsere eigene Reservierung
        raise
    return final


def format_size(n: int) -> str:
    kb = round(n / 1024, 1)
    if kb < 1024:
        return f"{kb:.1f} KB".replace(".", ",")
    return f"{n / 1024 / 1024:.1f} MB".replace(".", ",")


def _scan(dest_dir: Path | None) -> list[tuple]:
    """Gültige Sicherungen (reguläre, nicht leere Dateien), neueste zuerst: (p, size, m)."""
    if dest_dir is None or not Path(dest_dir).is_dir():
        return []
    found = []
    for p in Path(dest_dir).iterdir():
        m = NAME_RE.fullmatch(p.name)
        if not m:
            continue
        try:
            st = p.lstat()  # lstat: Symlinks werden nicht verfolgt
        except OSError:
            continue  # zwischenzeitlich gelöscht
        if stat.S_ISREG(st.st_mode) and st.st_size > 0:
            found.append(((m[1], m[2], int(m[3] or 1)), p, st.st_size, m))
    found.sort(key=lambda t: t[0], reverse=True)
    return [(p, size, m) for _, p, size, m in found]


def list_backups(dest_dir: Path | None) -> list[dict]:
    """Bis zu 50 Sicherungen, neueste zuerst. Nur Dateien mit passendem Namen."""
    out = []
    for p, size, m in _scan(dest_dir)[:MAX_LISTED]:
        d, t = m[1], m[2]
        out.append({
            "name": p.name, "size": format_size(size),
            "stamp": f"{d[6:]}.{d[4:6]}.{d[:4]} {t[:2]}:{t[2:4]}:{t[4:]}",
        })
    return out


def delete_old(dest_dir: Path | None, keep: int) -> int:
    """Löscht alle außer den neuesten keep Sicherungen (mindestens 1 bleibt). Anzahl."""
    deleted = 0
    for p, _, _ in _scan(dest_dir)[max(1, keep):]:
        try:
            p.unlink()
        except OSError:
            continue
        deleted += 1
    return deleted
