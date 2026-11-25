"""Beitragsprüfung: Monatsstatus, automatische Verteilung, Zahlungshistorie.

Beträge sind int-Cent. Als bezahlt zählen nur Verteilungen mit
status='confirmed'. Buchungen werden nie geändert, nur ihre Verteilungen.

Automatische Verteilung eines Zahlungseingangs (#27). Bezeichnungen:
start = max(Prüfungsstart, Beitragsbeginn des Mitglieds), P = Buchungsmonat,
offener Monat = Monat ab start mit Soll minus bestätigt (andere Buchungen) > 0.

1. Vor dem Prüfungsstart (P < Prüfungsstart): keine Verteilung, kein
   Vorschlag. Die Zahlung bleibt dem Mitglied zugeordnet (Historie).
2. Ist P offen, geht der Betrag zuerst dorthin, der Rest an die ältesten
   anderen offenen Monate (Rückstand), danach an folgende Monate
   (Vorauszahlung), jeder Monat bis zu seinem vollen Fehlbetrag.
3. Ist P nicht offen: älteste offene Monate aufsteigend, dann folgende.
4. Folgemonate höchstens MAX_MONTHS_AHEAD Monate nach max(P, start); nach
   dem Austrittsmonat ist kein Monat mehr fällig.
5. Eindeutig (automatisch bestätigt, source='auto'): alles verteilt und
   jeder Monat bekommt seinen vollen Fehlbetrag, oder eine einzelne
   Teilzahlung (Betrag kleiner als der Fehlbetrag des ersten Monats).
6. Sonst Vorschlag zur Prüfung (status='proposed'): ein Rest geht in den
   letzten verteilten Monat, ohne verteilten Monat in P. Die Summe ist
   immer der Buchungsbetrag.
7. Bestätigte Zeilen bleiben unangetastet, ersetzt werden nur Vorschläge.
8. source='manual' heißt: der Nutzer hat entschieden (Bestätigen oder
   Rückgängig). Automatische Läufe fassen nur Buchungen ohne Zeilen oder
   mit reinen Vorschlägen von KASSIVO (source='auto') an.

Verworfen: nur den exakten frühesten Monat im Buchungsmonat bestätigen (zu
viel Handarbeit, #27) und den Monat nach einer Zahlung am Monatsende
bevorzugen (294 von 344 echten Zahlungen kamen am 1. bis 5. des Monats).

Dieses Modul importiert matching nicht (matching ruft propose_allocations).
"""
import re
from dataclasses import dataclass
from datetime import date

from kassivo.db import get_setting, set_setting
from kassivo.members import expected_cents, list_members
from kassivo.months import add_months, month_end, month_range

CHECK_START_KEY = "check_start"
MAX_MONTHS_AHEAD = 36
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


REASON_AMOUNT = "Betrag passt nicht zu ganzen Monatsbeiträgen"
REASON_NO_MONTH = "Kein offener Monat (Mitglied ausgetreten oder Zahlung ohne fälligen Monat)"
REASON_REST = "Rest ohne offenen Monat"
REASON_MANUAL = "Automatische Verteilung rückgängig gemacht: bitte prüfen und bestätigen"
REASON_OUTSIDE = "Liegt vor dem Prüfungsstart: wird beim automatischen Verteilen entfernt"


@dataclass(frozen=True)
class _Plan:
    allocs: list[tuple[str, int]]
    outside: bool = False
    reason: str | None = None  # None = eindeutig

    @property
    def clean(self) -> bool:
        return not self.outside and self.reason is None


def _start_month(conn, member_id: int, booking_month: str) -> tuple[str | None, str]:
    """(Prüfungsstart, start). start = max(Prüfungsstart, Beitragsbeginn)."""
    check_start = get_check_start(conn)
    fee_start = conn.execute(
        "select fee_start from member where id=?", (member_id,)
    ).fetchone()["fee_start"]
    bounds = [m for m in (check_start, fee_start) if m]
    return check_start, (max(bounds) if bounds else booking_month)


def _order(booking_month: str, start: str):
    """Monate in Verteilreihenfolge: P zuerst (falls ab start), dann aufsteigend."""
    limit = add_months(max(start, booking_month), MAX_MONTHS_AHEAD)
    if booking_month >= start:
        yield booking_month
    for month in month_range(start, limit):
        if month != booking_month:
            yield month


def _compute(conn, tx) -> _Plan:
    """Verteilung nach den Regeln im Modulkopf. Nur lesend."""
    member_id, amount = tx["member_id"], tx["amount_cents"]
    booking_month = tx["booking_date"][:7]
    check_start, start = _start_month(conn, member_id, booking_month)
    if check_start and booking_month < check_start:
        return _Plan([], outside=True)

    allocs: dict[str, int] = {}
    remaining, full, first_shortfall = amount, True, None
    for month in _order(booking_month, start):
        if remaining == 0:
            break
        shortfall = expected_cents(conn, member_id, month) - _confirmed_paid(
            conn, member_id, month, exclude_tx=tx["id"])
        if shortfall <= 0:
            continue
        if first_shortfall is None:
            first_shortfall = shortfall
        take = min(shortfall, remaining)
        full = full and take == shortfall
        allocs[month] = take
        remaining -= take

    reason = None
    if not allocs:
        reason = REASON_NO_MONTH
    elif remaining:
        reason = REASON_REST
    elif not full and amount >= first_shortfall:
        reason = REASON_AMOUNT  # z. B. 20,00 = 17,00 + 3,00
    if remaining:
        last = max(allocs) if allocs else booking_month
        allocs[last] = allocs.get(last, 0) + remaining
    return _Plan(sorted(allocs.items()), reason=reason)


def _has_confirmed(conn, tx_id: int) -> bool:
    return conn.execute(
        "select 1 from allocation where tx_id=? and status='confirmed'", (tx_id,)
    ).fetchone() is not None


def _insert(conn, tx, allocs, status: str, source: str) -> None:
    conn.executemany(
        "insert into allocation(tx_id, member_id, month, cents, status, source)"
        " values(?,?,?,?,?,?)",
        [(tx["id"], tx["member_id"], m, c, status, source) for m, c in allocs],
    )


def propose_allocations(conn, tx_id: int, *, commit: bool = True,
                        auto: bool = True) -> list[tuple[str, int]]:
    """Verteilung berechnen und speichern. Eindeutiges wird mit auto=True bestätigt.

    auto=False speichert immer einen Vorschlag des Nutzers (source='manual', für
    Rückgängig). Gibt die gespeicherte Verteilung zurück, leer vor dem Prüfungsstart.
    """
    tx = _incoming_tx(conn, tx_id)
    if _has_confirmed(conn, tx_id):
        return _stored(conn, tx_id)  # Bestätigtes bleibt unangetastet
    conn.execute(
        "delete from allocation where tx_id=? and status='proposed'", (tx_id,)
    )
    plan = _compute(conn, tx)
    if auto and plan.clean:
        _insert(conn, tx, plan.allocs, "confirmed", "auto")
    else:
        _insert(conn, tx, plan.allocs, "proposed", "auto" if auto else "manual")
    if commit:
        conn.commit()
    return plan.allocs


@dataclass(frozen=True)
class AutoResult:
    total: int      # geprüfte Buchungen
    confirmed: int  # automatisch verteilt
    review: int     # bleiben zur Prüfung
    outside: int    # vor dem Prüfungsstart, ohne Verteilung


# Buchungen, die automatische Läufe anfassen dürfen: Eingang mit Mitglied, ohne
# Bestätigtes und ohne Vorschlag des Nutzers (also ohne Zeilen oder nur auto-Vorschläge).
_PENDING_SQL = (
    'select t.id from "transaction" t'
    " where t.amount_cents > 0 and t.member_id is not null"
    " and not exists(select 1 from allocation a where a.tx_id=t.id"
    " and (a.status='confirmed' or a.source<>'auto'))"
    " order by t.booking_date, t.id"
)


def auto_distribute_pending(conn) -> AutoResult:
    """Regeln auf alle offenen Buchungen anwenden, alles oder nichts in einem Commit.

    Älteste zuerst, damit spätere Zahlungen die früheren Ergebnisse sehen.
    Bestätigtes und Vorschläge nach Rückgängig bleiben unangetastet. Buchungen
    ohne Zeilen zählen mit (zum Beispiel nach einem Import ohne Verteilung).
    """
    ids = [r["id"] for r in conn.execute(_PENDING_SQL)]
    confirmed = review = outside = 0
    try:
        for tx_id in ids:
            if not propose_allocations(conn, tx_id, commit=False, auto=True):
                outside += 1
            elif _has_confirmed(conn, tx_id):
                confirmed += 1
            else:
                review += 1
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return AutoResult(len(ids), confirmed, review, outside)


def repropose_pending(conn) -> int:
    """Wie auto_distribute_pending, z. B. nach geändertem Prüfungsstart.

    Gibt die Zahl geprüfter Buchungen zurück. Verworfen: das in
    set_check_start erledigen (die Einstellung bliebe nicht rein).
    """
    return auto_distribute_pending(conn).total


def review_reason(conn, tx_id: int) -> str | None:
    """Kurzer Grund, warum ein Vorschlag geprüft werden muss. Nur lesend.

    None, wenn die Buchung keinen Vorschlag hat oder die Verteilung eindeutig ist.
    """
    rows = conn.execute(
        "select status, source from allocation where tx_id=?", (tx_id,)).fetchall()
    if not rows or any(r["status"] == "confirmed" for r in rows):
        return None
    tx = conn.execute(
        'select id, amount_cents, member_id, booking_date from "transaction" where id=?',
        (tx_id,)).fetchone()
    if tx is None or tx["member_id"] is None or tx["amount_cents"] <= 0:
        return None
    if any(r["source"] == "manual" for r in rows):
        return REASON_MANUAL
    plan = _compute(conn, tx)
    return REASON_OUTSIDE if plan.outside else plan.reason


def _kind(booking_month: str, allocs, expected: int) -> str:
    if len(allocs) == 1 and allocs[0][1] < expected:
        return "Teilzahlung"
    months = [m for m, _ in allocs]
    if any(m < booking_month for m in months):
        return "Rückstand"
    if any(m > booking_month for m in months):
        return "Vorauszahlung"
    return "Normal"


def auto_rows(conn, limit: int = 200) -> list[dict]:
    """Automatisch bestätigte Buchungen, neueste zuerst. Nur lesend."""
    txs = conn.execute(
        "select t.id, t.booking_date, t.amount_cents, t.cp_name, t.member_id,"
        ' m.name as member_name from "transaction" t join member m on m.id = t.member_id'
        " where exists(select 1 from allocation a where a.tx_id=t.id"
        " and a.status='confirmed' and a.source='auto')"
        " order by t.booking_date desc, t.id desc limit ?", (int(limit),),
    ).fetchall()
    out = []
    for t in txs:
        allocs = [(r["month"], r["cents"]) for r in conn.execute(
            "select month, cents from allocation where tx_id=? and status='confirmed'"
            " order by month, id", (t["id"],))]
        expected = expected_cents(conn, t["member_id"], allocs[0][0])
        out.append({
            "tx_id": t["id"], "booking_date": t["booking_date"],
            "amount_cents": t["amount_cents"], "cp_name": t["cp_name"],
            "member_id": t["member_id"], "member_name": t["member_name"],
            "allocations": allocs,
            "kind": _kind(t["booking_date"][:7], allocs, expected),
        })
    return out


def undo_auto(conn, tx_id: int) -> None:
    """Automatische Verteilung zurücknehmen: daraus wird ein Vorschlag des Nutzers.

    Nur für Buchungen, deren bestätigte Zeilen alle von KASSIVO stammen;
    Bestätigungen des Nutzers bleiben unangetastet (ValueError). Ein Commit.
    """
    rows = conn.execute(
        "select status, source from allocation where tx_id=?", (tx_id,)).fetchall()
    confirmed = [r for r in rows if r["status"] == "confirmed"]
    if not confirmed or any(r["source"] != "auto" for r in confirmed):
        raise ValueError("Nur automatisch verteilte Zahlungen können rückgängig gemacht werden.")
    try:
        conn.execute("delete from allocation where tx_id=?", (tx_id,))
        propose_allocations(conn, tx_id, commit=False, auto=False)
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def distribution_counts(conn, tx_ids) -> tuple[int, int]:
    """(automatisch verteilt, zur Prüfung) unter den gegebenen Buchungen. Nur lesend."""
    wanted = set(tx_ids)
    auto, review = set(), set()
    for r in conn.execute("select distinct tx_id, status, source from allocation"):
        if r["tx_id"] not in wanted:
            continue
        if r["status"] == "proposed":
            review.add(r["tx_id"])
        elif r["source"] == "auto":
            auto.add(r["tx_id"])
    return len(auto - review), len(review)


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
        "insert into allocation(tx_id, member_id, month, cents, status, source)"
        " values(?,?,?,?,'confirmed','manual')",
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
