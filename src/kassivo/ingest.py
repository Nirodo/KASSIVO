"""Import von CAMT-052-Dateien (ZIP, einzelne XML oder Ordner) mit Duplikaterkennung.

Nur lesend gegenüber der Bank. Alles in einer DB-Transaktion: erst alle Dateien
parsen, dann schreiben; import_and_assign nimmt Regeln, Zuordnung und
Verteilung in dieselbe Transaktion (#7). Meldungen enthalten nie volle IBAN,
Namen oder Zweck.
"""
import hashlib
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kassivo import categories, dues, ledger, matching, periods
from kassivo.camt import CamtError, ParsedStatement, parse_camt052
from kassivo.iban import mask_iban  # bleibt als ingest.mask_iban importierbar

MAX_XML_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_FILES = 5000
NOT_FOUND = "Datei nicht gefunden oder nicht lesbar"
NO_XML = "Keine XML-Dateien gefunden"
TOO_BIG_TOTAL = "Import insgesamt zu groß"


@dataclass
class ImportResult:
    batch_id: int
    new: int
    duplicates: int
    warnings: list[str]
    new_tx_ids: list[int]


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
    except OSError:
        raise CamtError(f"{NOT_FOUND}: {path.name}") from None
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
            except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, zlib.error, EOFError):
                raise CamtError(f"{info.filename}: nicht lesbar") from None
            if len(data) > MAX_XML_BYTES:
                raise CamtError(f"{info.filename}: Datei zu groß")
            total += len(data)
            if total > MAX_TOTAL_BYTES:  # Header können lügen: tatsächlich gelesene Bytes zählen
                raise CamtError(TOO_BIG_TOTAL)
            out.append((info.filename, data))
    return out


def _read_file(path: Path) -> tuple[str, bytes]:
    """Begrenzt lesen: eine nach der Prüfung gewachsene Datei wird nie ganz geladen."""
    try:
        with path.open("rb") as f:
            data = f.read(MAX_XML_BYTES + 1)
    except OSError:  # gelöscht oder gesperrt, auch zwischen Auflisten und Lesen
        raise CamtError(f"{NOT_FOUND}: {path.name}") from None
    if len(data) > MAX_XML_BYTES:
        raise CamtError(f"{path.name}: Datei zu groß")
    return path.name, data


def _collect(path: Path) -> list[tuple[str, bytes]]:
    if not path.exists():
        raise CamtError(f"{NOT_FOUND}: {path.name}")
    try:
        return _collect_existing(path)
    except OSError:  # Ordner nicht lesbar oder Datei verschwunden
        raise CamtError(f"{NOT_FOUND}: {path.name}") from None


def _collect_existing(path: Path) -> list[tuple[str, bytes]]:
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.is_file() and _is_xml(p.name))
        _check_count(len(files))
        size = 0
        for p in files:
            try:
                size += p.stat().st_size
            except OSError:  # zwischen Auflisten und stat() verschwunden
                raise CamtError(f"{NOT_FOUND}: {p.name}") from None
        if size > MAX_TOTAL_BYTES:
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


def _zero_warning(st: ParsedStatement) -> str | None:
    if not st.skipped_zero:
        return None
    n = st.skipped_zero
    wort = "Eintrag" if n == 1 else "Einträge"
    return f"Konto {mask_iban(st.account_iban)}, {st.skipped_date.isoformat()}: {n} {wort} mit 0,00 € übersprungen"


def _nostatus_warning(st: ParsedStatement) -> str | None:
    if not st.skipped_nostatus:
        return None
    n = st.skipped_nostatus
    wort = "Eintrag" if n == 1 else "Einträge"
    return f"Konto {mask_iban(st.account_iban)}: {n} {wort} ohne Status übersprungen"


def _refconflict_warning(st: ParsedStatement, n: int, day: str) -> str:
    return (f"Konto {mask_iban(st.account_iban)}, {day}: {n} Buchung(en) mit bereits bekannter "
            "Bankreferenz, aber anderem Betrag oder Datum wurden nicht übernommen")


def _account_id(conn, iban: str, label: str) -> int:
    row = conn.execute("select id from account where iban=?", (iban,)).fetchone()
    if row:
        return row[0]
    acc = conn.execute("insert into account(iban, label) values(?,?)", (iban, label)).lastrowid
    ledger.bank_ledger_account(conn, acc)  # jedes Bankkonto hat genau ein Aktivkonto
    return acc


def _ref_conflict(conn, acc: int, key: str, tx) -> bool:
    """Gleiche Bankreferenz, aber anderer Betrag oder anderes Buchungsdatum als gespeichert."""
    row = conn.execute(
        'select amount_cents, booking_date from "transaction" where account_id=? and dedupe_key=?',
        (acc, key),
    ).fetchone()
    return row is not None and (row[0], row[1]) != (tx.amount_cents, tx.booking_date.isoformat())


def _prepare(conn, path: Path, caller: str) -> list[ParsedStatement]:
    if conn.in_transaction:  # `with conn` würde fremde offene Änderungen mit-committen
        raise RuntimeError(f"{caller} darf nicht innerhalb einer offenen Transaktion laufen")
    return _parse_all(_collect(path))  # wirft, bevor geschrieben wird


def _write(conn, path: Path, statements, bank_label: str) -> ImportResult:
    """Schreibt Stapel und Buchungen. Ohne commit (Transaktion des Aufrufers)."""
    warnings = [w for st in statements for w in (_balance_warning(st), _zero_warning(st), _nostatus_warning(st)) if w]
    new_ids: list[int] = []
    dups = 0
    batch_id = conn.execute(
        "insert into import_batch(filename, imported_at) values(?,?)",
        (path.name, datetime.now().isoformat(timespec="seconds")),
    ).lastrowid
    for st in statements:
        conflicts: list[str] = []  # Buchungstage der Bankreferenz-Konflikte
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
                # zu jedem Datum eine Periode, auch wenn der Umsatz "Neu" bleibt
                periods.ensure_period(conn, tx.booking_date.isoformat())
            else:
                dups += 1
                if tx.bank_ref and _ref_conflict(conn, acc, key, tx):
                    conflicts.append(tx.booking_date.isoformat())
        if conflicts:
            warnings.append(_refconflict_warning(st, len(conflicts), max(conflicts)))
    conn.execute(
        "update import_batch set new_count=?, dup_count=? where id=?",
        (len(new_ids), dups, batch_id),
    )
    closed = _in_closed_period(conn, new_ids)
    if closed:
        warnings.append(f"{closed} Umsätze liegen in einer abgeschlossenen Periode und wurden "
                        "nicht verbucht")
    return ImportResult(batch_id, len(new_ids), dups, warnings, new_ids)


def _in_closed_period(conn, tx_ids: list[int]) -> int:
    """Zahl der Umsätze, deren Datum in einer abgeschlossenen Periode liegt."""
    closed = conn.execute("select start_date, end_date from period"
                          " where status = 'closed'").fetchall()
    if not closed or not tx_ids:
        return 0
    wanted = set(tx_ids)
    return sum(1 for r in conn.execute('select id, booking_date from "transaction"')
               if r[0] in wanted and any(s <= r[1] <= e for s, e in closed))


def import_path(conn, path: Path, bank_label: str = "Sparkasse") -> ImportResult:
    """Nur Buchungen schreiben, ohne Regeln und Zuordnung (eine Transaktion)."""
    path = Path(path)
    statements = _prepare(conn, path, "import_path")
    with conn:  # eine Transaktion: commit bei Erfolg, rollback bei jedem Fehler
        return _write(conn, path, statements, bank_label)


@dataclass
class ImportOutcome:
    result: ImportResult
    categorized: int
    assigned: int
    redistributed: bool  # abgeleiteter Prüfungsstart vorverlegt, alles neu verteilt


def import_and_assign(conn, path: Path, bank_label: str = "Sparkasse") -> ImportOutcome:
    """Import mit Regeln, Zuordnung und Verteilung in einem Commit (#7).

    Alles oder nichts: scheitert ein Schritt, bleibt die Datenbank wie vorher,
    und ein erneuter Import legt die Buchungen vollständig neu an. Ein
    wiederholter Import derselben Datei ändert nur den Stapel (0 neu).
    Verworfen: Einzel-Commits mit Hinweis und Knopf "Regeln erneut anwenden"
    (Teilzustand sichtbar, Duplikaterkennung blockiert den erneuten Import).
    """
    path = Path(path)
    statements = _prepare(conn, path, "import_and_assign")
    derived_before = None if dues.check_start_is_set(conn) else dues.get_check_start(conn)
    with conn:  # commit bei Erfolg, rollback bei jedem Fehler
        result = _write(conn, path, statements, bank_label)
        categorized = categories.apply_rules(conn, result.new_tx_ids)
        assigned = matching.auto_assign(conn, result.new_tx_ids, commit=False)
        redistributed = (not dues.check_start_is_set(conn)
                         and dues.get_check_start(conn) != derived_before)
        if redistributed:
            # abgeleiteter Prüfungsstart ist früher geworden: Abgeleitetes folgt
            dues.redistribute_all(conn, commit=False)
    return ImportOutcome(result, categorized, assigned, redistributed)
