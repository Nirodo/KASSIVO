"""Import von CAMT-052-Dateien (ZIP, einzelne XML oder Ordner) mit Duplikaterkennung.

Nur lesend gegenüber der Bank. Alles in einer DB-Transaktion: erst alle Dateien
parsen, dann schreiben. Meldungen enthalten nie volle IBAN, Namen oder Zweck.
"""
import hashlib
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kassivo.camt import CamtError, ParsedStatement, parse_camt052

MAX_XML_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_FILES = 5000
NO_XML = "Keine XML-Dateien gefunden"
TOO_BIG_TOTAL = "Import insgesamt zu groß"


@dataclass
class ImportResult:
    batch_id: int
    new: int
    duplicates: int
    warnings: list[str]
    new_tx_ids: list[int]


def mask_iban(iban: str) -> str:
    return f"{iban[:4]}…{iban[-4:]}" if len(iban) > 8 else "…"


def _is_xml(name: str) -> bool:
    return name.lower().endswith(".xml")


def _check_count(n: int) -> None:
    if n > MAX_FILES:
        raise CamtError(f"zu viele XML-Dateien (> {MAX_FILES})")


def _read_zip(path: Path) -> list[tuple[str, bytes]]:
    """Liest XML-Einträge im Speicher. Nie entpacken, Größe vor und beim Lesen begrenzen."""
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise CamtError(f"{path.name}: kein gültiges ZIP") from None
    with z:
        infos = [i for i in z.infolist() if not i.is_dir() and _is_xml(i.filename)]
        _check_count(len(infos))
        if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
            raise CamtError(TOO_BIG_TOTAL)
        out, total = [], 0
        for info in infos:
            if info.file_size > MAX_XML_BYTES:
                raise CamtError(f"{info.filename}: Datei zu groß")
            try:
                with z.open(info) as f:
                    data = f.read(MAX_XML_BYTES + 1)
            except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
                raise CamtError(f"{info.filename}: nicht lesbar") from None
            if len(data) > MAX_XML_BYTES:
                raise CamtError(f"{info.filename}: Datei zu groß")
            total += len(data)
            if total > MAX_TOTAL_BYTES:  # Header können lügen: tatsächlich gelesene Bytes zählen
                raise CamtError(TOO_BIG_TOTAL)
            out.append((info.filename, data))
    return out


def _read_file(path: Path) -> tuple[str, bytes]:
    if path.stat().st_size > MAX_XML_BYTES:
        raise CamtError(f"{path.name}: Datei zu groß")
    return path.name, path.read_bytes()


def _collect(path: Path) -> list[tuple[str, bytes]]:
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.is_file() and _is_xml(p.name))
        _check_count(len(files))
        if sum(p.stat().st_size for p in files) > MAX_TOTAL_BYTES:
            raise CamtError(TOO_BIG_TOTAL)
        found, total = [], 0
        for p in files:
            found.append(_read_file(p))
            total += len(found[-1][1])
            if total > MAX_TOTAL_BYTES:  # Datei kann seit stat() gewachsen sein
                raise CamtError(TOO_BIG_TOTAL)
    elif _is_xml(path.name):
        found = [_read_file(path)]
    else:
        found = _read_zip(path)
    if not found:
        raise CamtError(NO_XML)
    return found


def _parse_all(files: list[tuple[str, bytes]]) -> list[ParsedStatement]:
    out = []
    for name, data in files:
        try:
            out.append(parse_camt052(data))
        except CamtError as e:
            raise CamtError(f"{name}: {e}") from None
    return out


def _dedupe_keys(st: ParsedStatement) -> list[str]:
    """bank_ref, sonst sha256 über booking_date|amount|cp_iban|purpose|n (n: laufend je Datei)."""
    seen: dict[str, int] = {}
    keys = []
    for tx in st.entries:
        if tx.bank_ref:
            keys.append(tx.bank_ref)
            continue
        base = f"{tx.booking_date.isoformat()}|{tx.amount_cents}|{tx.cp_iban or ''}|{tx.purpose}"
        n = seen.get(base, 0)
        seen[base] = n + 1
        keys.append(hashlib.sha256(f"{base}|{n}".encode("utf-8")).hexdigest())
    return keys


def _balance_warning(st: ParsedStatement) -> str | None:
    if st.opening_cents is None or st.closing_cents is None:
        return None
    if st.opening_cents + sum(t.amount_cents for t in st.entries) == st.closing_cents:
        return None
    day = max(t.booking_date for t in st.entries).isoformat() if st.entries else "ohne Buchungen"
    return f"Saldo stimmt nicht: Konto {mask_iban(st.account_iban)}, {day}"


def _account_id(conn, iban: str, label: str) -> int:
    row = conn.execute("select id from account where iban=?", (iban,)).fetchone()
    if row:
        return row[0]
    return conn.execute("insert into account(iban, label) values(?,?)", (iban, label)).lastrowid


def import_path(conn, path: Path, bank_label: str = "Sparkasse") -> ImportResult:
    path = Path(path)
    statements = _parse_all(_collect(path))  # wirft, bevor geschrieben wird
    warnings = [w for w in map(_balance_warning, statements) if w]
    new_ids: list[int] = []
    dups = 0
    with conn:  # eine Transaktion: commit bei Erfolg, rollback bei jedem Fehler
        batch_id = conn.execute(
            "insert into import_batch(filename, imported_at) values(?,?)",
            (path.name, datetime.now().isoformat(timespec="seconds")),
        ).lastrowid
        for st in statements:
            acc = _account_id(conn, st.account_iban, bank_label)
            for tx, key in zip(st.entries, _dedupe_keys(st)):
                cur = conn.execute(
                    'insert into "transaction"(account_id, booking_date, value_date, amount_cents,'
                    " bank_ref, cp_name, cp_iban, purpose, dedupe_key, batch_id)"
                    " values(?,?,?,?,?,?,?,?,?,?) on conflict(account_id, dedupe_key) do nothing",
                    (acc, tx.booking_date.isoformat(),
                     tx.value_date.isoformat() if tx.value_date else None,
                     tx.amount_cents, tx.bank_ref, tx.cp_name, tx.cp_iban, tx.purpose,
                     key, batch_id),
                )
                if cur.rowcount == 1:
                    new_ids.append(cur.lastrowid)
                else:
                    dups += 1
        conn.execute(
            "update import_batch set new_count=?, dup_count=? where id=?",
            (len(new_ids), dups, batch_id),
        )
    return ImportResult(batch_id, len(new_ids), dups, warnings, new_ids)
