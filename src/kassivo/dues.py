"""Beitragsprüfung: Monatsstatus, Verteilungsvorschlag, Zahlungshistorie.

Beträge sind int-Cent. Als bezahlt zählen nur Verteilungen mit
status='confirmed'. Vorschläge ('proposed') bestätigt der Nutzer, außer ein
Eingang deckt genau den frühesten offenen Monat und dieser ist sein
Buchungsmonat. Buchungen werden nie
geändert, nur ihre Verteilungen.

Dieses Modul importiert matching nicht (matching ruft propose_allocations).
"""
import re
from dataclasses import dataclass
from datetime import date

from kassivo.db import get_setting, set_setting
from kassivo.members import expected_cents, list_members
from kassivo.months import add_months, month_end, month_range

CHECK_START_KEY = "check_start"
MAX_MONTHS_AHEAD = 24
_MONTH_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


@dataclass(frozen=True)
class MonthStatus:
    month: str
    expected: int
    paid: int
    status: str  # NONE, PAID, PARTIAL, OPEN, OVERDUE


def _valid_month(m) -> bool:
    return isinstance(m, str) and _MONTH_RE.fullmatch(m) is not None


def get_check_start(conn) -> str | None:
    """Gespeicherter Prüfungsstart, sonst der Monat der ältesten Buchung.

    Verworfen: Januar des ältesten Buchungsjahres. Das zog bei langjährigen
    Mitgliedern Monate vor dem ersten Import in die Prüfung.
    """
    stored = get_setting(conn, CHECK_START_KEY)
    if stored:
        return stored
    row = conn.execute('select min(booking_date) from "transaction"').fetchone()
    return row[0][:7] if row[0] else None


def check_start_is_set(conn) -> bool:
    """True nur, wenn der Nutzer den Prüfungsstart ausdrücklich gespeichert hat."""
    return bool(get_setting(conn, CHECK_START_KEY))


def set_check_start(conn, month: str) -> None:
    if not _valid_month(month):
        raise ValueError("Prüfungsstart muss im Format JJJJ-MM sein")
    set_setting(conn, CHECK_START_KEY, month)


def _confirmed_paid(conn, member_id: int, month: str, exclude_tx: int | None = None) -> int:
    row = conn.execute(
        "select coalesce(sum(cents), 0) from allocation"
        " where member_id=? and month=? and status='confirmed'"
        " and (? is null or tx_id<>?)",
        (member_id, month, exclude_tx, exclude_tx),
    ).fetchone()
    return row[0]


def month_status(conn, member_id: int, month: str, today: date) -> MonthStatus:
    start = get_check_start(conn)
    if start and month < start:
        return MonthStatus(month, 0, 0, "NONE")
    expected = expected_cents(conn, member_id, month)
    paid = _confirmed_paid(conn, member_id, month)
    if expected == 0:
        status = "NONE"
    elif paid >= expected:
        status = "PAID"
    elif month_end(month) < today:
        status = "OVERDUE"  # auch Teilzahlung eines vergangenen Monats
    elif paid > 0:
        status = "PARTIAL"
    else:
        status = "OPEN"
    return MonthStatus(month, expected, paid, status)


def _incoming_tx(conn, tx_id: int):
    tx = conn.execute(
        'select id, amount_cents, member_id, booking_date from "transaction"'
        " where id=?", (tx_id,),
    ).fetchone()
    if tx is None:
        raise ValueError(f"unbekannte Buchung {tx_id}")
    if tx["amount_cents"] <= 0:
        raise ValueError(f"Buchung {tx_id} ist kein Zahlungseingang")
    if tx["member_id"] is None:
        raise ValueError(f"Buchung {tx_id} ist keinem Mitglied zugeordnet")
    return tx


def _stored(conn, tx_id: int) -> list[tuple[str, int]]:
    return [
        (r["month"], r["cents"])
        for r in conn.execute(
            "select month, cents from allocation where tx_id=? order by month, id",
            (tx_id,),
        )
    ]


def _compute(conn, tx) -> tuple[list[tuple[str, int]], bool]:
    """Verteilung Monat für Monat ab dem frühesten Fehlbetrag.

    Liefert (Verteilung, exakter Treffer). Die Summe ist immer der
    Buchungsbetrag: ein Rest ohne fälligen Monat geht in den letzten
    verteilten Monat, sonst in den Buchungsmonat.
    """
    member_id, amount = tx["member_id"], tx["amount_cents"]
    booking_month = tx["booking_date"][:7]
    fee_start = conn.execute(
        "select fee_start from member where id=?", (member_id,)
    ).fetchone()["fee_start"]
    bounds = [m for m in (get_check_start(conn), fee_start) if m]
    start = max(bounds) if bounds else booking_month
    limit = add_months(max(start, booking_month), MAX_MONTHS_AHEAD)

    allocs: dict[str, int] = {}
    first_shortfall = first_month = None
    remaining = amount
    for month in month_range(start, limit):
        if remaining == 0:
            break
        shortfall = expected_cents(conn, member_id, month) - _confirmed_paid(
            conn, member_id, month, exclude_tx=tx["id"])
        if shortfall <= 0:
            continue
        if first_shortfall is None:
            first_shortfall, first_month = shortfall, month
        take = min(shortfall, remaining)
        allocs[month] = take
        remaining -= take
    if remaining:
        last = max(allocs) if allocs else booking_month
        allocs[last] = allocs.get(last, 0) + remaining
    result = sorted(allocs.items())
    # Nur eine Zahlung im eigenen Fälligkeitsmonat bestätigt sich selbst.
    # Rückstände, Vorauszahlungen und Zahlungen aus dem Vormonat bestätigt
    # der Nutzer (Spec: "Das Verteilen bestätigt der Nutzer").
    exact = (
        len(result) == 1
        and result[0][1] == amount
        and first_shortfall == amount
        and first_month == booking_month
        and result[0][0] == booking_month
    )
    return result, exact


def propose_allocations(conn, tx_id: int, *, commit: bool = True) -> list[tuple[str, int]]:
    tx = _incoming_tx(conn, tx_id)
    if conn.execute(
        "select 1 from allocation where tx_id=? and status='confirmed'", (tx_id,)
    ).fetchone():
        return _stored(conn, tx_id)  # Nutzerentscheidung bleibt unangetastet
    conn.execute(
        "delete from allocation where tx_id=? and status='proposed'", (tx_id,)
    )
    allocs, exact = _compute(conn, tx)
    status = "confirmed" if exact else "proposed"
    conn.executemany(
        "insert into allocation(tx_id, member_id, month, cents, status)"
        " values(?,?,?,?,?)",
        [(tx_id, tx["member_id"], m, c, status) for m, c in allocs],
    )
    if commit:
        conn.commit()
    return allocs


def repropose_pending(conn) -> int:
    """Offene Vorschläge neu berechnen, z. B. nach geändertem Prüfungsstart.

    Betrifft nur Zahlungseingänge mit Mitglied, mindestens einem Vorschlag und
    ohne bestätigte Zeile; Bestätigtes bleibt unangetastet. Älteste zuerst,
    damit frühere Zahlungen frühere Monate füllen. Alles oder nichts in einem
    Commit. Gibt die Zahl neu berechneter Buchungen zurück.

    Verworfen: das in set_check_start erledigen (Einstellung bliebe nicht
    rein) und alle Buchungen neu vorschlagen (Buchungen ohne Vorschlag hat
    der Nutzer bewusst geleert).
    """
    ids = [r["id"] for r in conn.execute(
        'select t.id from "transaction" t'
        " where t.amount_cents > 0 and t.member_id is not null"
        " and exists(select 1 from allocation a where a.tx_id=t.id and a.status='proposed')"
        " and not exists(select 1 from allocation a where a.tx_id=t.id"
        " and a.status='confirmed')"
        " order by t.booking_date, t.id"
    )]
    try:
        for tx_id in ids:
            propose_allocations(conn, tx_id, commit=False)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return len(ids)


def confirm_allocations(conn, tx_id: int, allocs: list[tuple[str, int]]) -> None:
    tx = _incoming_tx(conn, tx_id)
    if not allocs:
        raise ValueError("Verteilung ist leer")
    months = set()
    for month, cents in allocs:
        if not _valid_month(month):
            raise ValueError("Monat muss im Format JJJJ-MM sein")
        if type(cents) is not int or cents <= 0:
            raise ValueError("Beträge müssen positive ganze Cent sein")
        if month in months:
            raise ValueError(f"Monat {month} doppelt")
        months.add(month)
    if sum(c for _, c in allocs) != tx["amount_cents"]:
        raise ValueError("Summe der Verteilung entspricht nicht dem Buchungsbetrag")
    conn.execute("delete from allocation where tx_id=?", (tx_id,))
    conn.executemany(
        "insert into allocation(tx_id, member_id, month, cents, status)"
        " values(?,?,?,?,'confirmed')",
        [(tx_id, tx["member_id"], m, c) for m, c in allocs],
    )
    conn.commit()


def clear_allocations(conn, tx_id: int) -> int:
    """Löscht alle Verteilungen (proposed und confirmed) einer Buchung.

    Macht eine falsche Zuordnung korrigierbar: danach greift die Sperre in
    assign_member nicht mehr. member_id, Kategorie und gelernte IBAN bleiben,
    es wird nichts neu vorgeschlagen. Gibt die Zahl gelöschter Zeilen zurück.
    """
    if not conn.execute(
        'select 1 from "transaction" where id=?', (tx_id,)
    ).fetchone():
        raise ValueError(f"unbekannte Buchung {tx_id}")
    try:
        n = conn.execute("delete from allocation where tx_id=?", (tx_id,)).rowcount
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return n


def dues_grid(conn, year: int, today: date) -> list[dict]:
    members = sorted(list_members(conn), key=lambda r: (r["name"].casefold(), r["id"]))
    months = [f"{year:04d}-{i:02d}" for i in range(1, 13)]
    return [
        {
            "member_id": m["id"],
            "name": m["name"],
            "months": [month_status(conn, m["id"], mo, today) for mo in months],
        }
        for m in members
    ]


def proposed_months(conn, year: int) -> set[tuple[int, str]]:
    """(Mitglied, Monat) mit mindestens einem Vorschlag im Jahr. Nur lesend."""
    return {
        (r["member_id"], r["month"])
        for r in conn.execute(
            "select distinct member_id, month from allocation"
            " where status='proposed' and month >= ? and month <= ?",
            (f"{year:04d}-01", f"{year:04d}-12"),
        )
    }


def proposed_tx_count(conn) -> int:
    """Zahl der Buchungen mit mindestens einem offenen Vorschlag."""
    return conn.execute(
        "select count(distinct tx_id) from allocation where status='proposed'"
    ).fetchone()[0]


def payment_history(conn, member_id: int) -> list[dict]:
    txs = conn.execute(
        'select id, booking_date, amount_cents, cp_name, purpose from "transaction"'
        " where member_id=? order by booking_date desc, id desc",
        (member_id,),
    ).fetchall()
    out = []
    for t in txs:
        allocs = [
            (r["month"], r["cents"], r["status"])
            for r in conn.execute(
                "select month, cents, status from allocation"
                " where tx_id=? order by month, id", (t["id"],),
            )
        ]
        out.append({
            "tx_id": t["id"], "booking_date": t["booking_date"],
            "amount_cents": t["amount_cents"], "cp_name": t["cp_name"],
            "purpose": t["purpose"], "allocations": allocs,
        })
    return out
