"""Beitragsprüfung: Monatsstatus, automatische Verteilung, Zahlungshistorie.

Beträge sind int-Cent. Als bezahlt zählen nur Verteilungen mit
status='confirmed'. Buchungen werden nie geändert, nur ihre Verteilungen.

Automatische Verteilung eines Zahlungseingangs (#27). Bezeichnungen:
start = max(Prüfungsstart, Beitragsbeginn des Mitglieds), P = Buchungsmonat,
offener Monat = Monat ab start mit Soll minus bestätigt (andere Buchungen) > 0.

Grundsatz: Zeilen mit source='auto' (Vorschlag und bestätigt) sind abgeleitet.
Für ein Mitglied werden sie alle neu berechnet (rebuild_member, kanonisch wie
redistribute_all), wenn sich seine Zuordnungen ändern oder der Nutzer bei einer
seiner Zahlungen entscheidet (confirm_allocations, undo_auto,
clear_allocations) oder seine Stammdaten ändern (set_member_type, leave_member,
Beitragsbeginn in update_member); für alle Mitglieder bei gespeichertem
Prüfungsstart, bei geändertem Beitrag (set_fee) und wenn ein Import den
abgeleiteten Prüfungsstart vorverlegt. Automatische Verteilungen belegen so
keinen Monat doppelt. Zeilen mit source='manual'
sind Entscheidungen des Nutzers (Bestätigen, Rückgängig, Verteilung aufheben,
Neu vorschlagen) und werden von keinem automatischen Weg angefasst.

1. Vor dem Prüfungsstart (P < Prüfungsstart): keine Verteilung, kein
   Vorschlag. Die Zahlung bleibt dem Mitglied zugeordnet (Historie).
2. Angefangener Monat: Gibt es Monate M mit start <= M < P, die teilweise
   bezahlt sind, und übersteigt der Betrag den Fehlbetrag nicht, geht alles in
   den ältesten solchen Monat.
3. Ist P offen, geht der Betrag zuerst dorthin, der Rest an die ältesten
   anderen offenen Monate (Rückstand), danach an folgende Monate
   (Vorauszahlung), jeder Monat bis zu seinem vollen Fehlbetrag.
4. Ist P nicht offen: älteste offene Monate aufsteigend, dann folgende.
5. Folgemonate höchstens MAX_MONTHS_AHEAD Monate nach max(P, start); nach
   dem Austrittsmonat ist kein Monat mehr fällig.
6. Eindeutig (automatisch bestätigt): alles verteilt, höchstens
   MAX_AUTO_MONTHS Monate, und jeder Monat bekommt seinen vollen Fehlbetrag
   oder es ist eine einzelne Teilzahlung.
7. Sonst Vorschlag zur Prüfung (status='proposed'): ein Rest geht in den
   letzten verteilten Monat, ohne verteilten Monat in P. Die Summe ist
   immer der Buchungsbetrag.
8. Läufe über mehrere Buchungen gehen chronologisch und wiederholen sich, bis
   nichts Neues mehr bestätigt wird (Fixpunkt: ein zweiter Lauf ändert nichts).

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
REASON_TOO_MANY = "Mehr als 12 Monate im Voraus"
REASON_MANUAL = "Von Ihnen zur Prüfung gestellt: bitte Verteilung prüfen und bestätigen"
REASON_OUTSIDE = "Liegt vor dem Prüfungsstart: wird beim automatischen Verteilen entfernt"
REASON_STALE = "Wird beim nächsten Lauf automatisch verteilt"
MAX_AUTO_MONTHS = 12


@dataclass(frozen=True)
class _Plan:
    allocs: list[tuple[str, int]]
    outside: bool = False
    reason: str | None = None  # None = eindeutig

    @property
    def clean(self) -> bool:
        return not self.outside and self.reason is None


class _Member:
    """Zwischenspeicher eines Mitglieds: Soll je Monat und bestätigt Bezahltes je Monat.

    Gilt nur, solange niemand außerhalb von _Ctx bestätigte Zeilen schreibt oder
    löscht. Bezahlt: Monat -> {Buchung: Cent}, aus allocation.member_id wie
    _confirmed_paid.
    """

    def __init__(self, conn, member_id: int):
        m = conn.execute("select fee_start, austritt from member where id=?",
                         (member_id,)).fetchone()
        self.fee_start = m["fee_start"] if m else None
        self.austritt = m["austritt"][:7] if m and m["austritt"] else None
        self.exists = m is not None
        self.types = [(r["valid_from"], r["type_id"]) for r in conn.execute(
            "select valid_from, type_id from member_type_history where member_id=?"
            " order by valid_from", (member_id,))]
        self.fees: dict[int, list[tuple[str, int]]] = {}
        for r in conn.execute("select type_id, valid_from, cents from fee_rate"
                              " order by valid_from"):
            self.fees.setdefault(r["type_id"], []).append((r["valid_from"], r["cents"]))
        self._expected: dict[str, int] = {}
        self.paid: dict[str, dict[int, int]] = {}
        for r in conn.execute("select tx_id, month, cents from allocation"
                              " where member_id=? and status='confirmed'", (member_id,)):
            by_tx = self.paid.setdefault(r["month"], {})
            by_tx[r["tx_id"]] = by_tx.get(r["tx_id"], 0) + r["cents"]

    def expected(self, month: str) -> int:
        """Wie members.expected_cents, ohne Abfragen."""
        if month in self._expected:
            return self._expected[month]
        value = 0
        if self.exists and not (self.fee_start and month < self.fee_start) and not (
                self.austritt and month > self.austritt):
            type_id = None
            for valid_from, tid in self.types:
                if valid_from <= month:
                    type_id = tid
            if type_id is not None:
                for valid_from, cents in self.fees.get(type_id, ()):
                    if valid_from <= month:
                        value = cents
        self._expected[month] = value
        return value

    def paid_without(self, month: str, tx_id: int) -> int:
        by_tx = self.paid.get(month)
        if not by_tx:
            return 0
        return sum(by_tx.values()) - by_tx.get(tx_id, 0)

    def add(self, tx_id: int, allocs) -> None:
        for month, cents in allocs:
            by_tx = self.paid.setdefault(month, {})
            by_tx[tx_id] = by_tx.get(tx_id, 0) + cents


class _Ctx:
    """Zwischenspeicher für einen Lauf: Prüfungsstart und Mitglieder."""

    def __init__(self, conn):
        self.conn = conn
        self.check_start = get_check_start(conn)
        self._members: dict[int, _Member] = {}

    def member(self, member_id: int) -> _Member:
        if member_id not in self._members:
            self._members[member_id] = _Member(self.conn, member_id)
        return self._members[member_id]


def _start_month(ctx: _Ctx, member: _Member, booking_month: str) -> str:
    """start = max(Prüfungsstart, Beitragsbeginn)."""
    bounds = [m for m in (ctx.check_start, member.fee_start) if m]
    return max(bounds) if bounds else booking_month


def _order(booking_month: str, start: str):
    """Monate in Verteilreihenfolge: P zuerst (falls ab start), dann aufsteigend."""
    limit = add_months(max(start, booking_month), MAX_MONTHS_AHEAD)
    if booking_month >= start:
        yield booking_month
    month = start
    while month <= limit:
        if month != booking_month:
            yield month
        month = add_months(month, 1)


def _part_paid_month(member: _Member, tx, start: str, booking_month: str) -> str | None:
    """Regel 2: ältester angefangener Monat vor P, den der Betrag nicht übersteigt.

    Nur Monate mit bestätigten Zahlungen kommen in Frage (paid > 0).
    """
    for month in sorted(m for m in member.paid if start <= m < booking_month):
        expected, paid = member.expected(month), member.paid_without(month, tx["id"])
        if 0 < paid < expected and tx["amount_cents"] <= expected - paid:
            return month
    return None


def _compute(conn, tx, ctx: _Ctx | None = None) -> _Plan:
    """Verteilung nach den Regeln im Modulkopf. Nur lesend."""
    ctx = ctx or _Ctx(conn)
    member = ctx.member(tx["member_id"])
    amount = tx["amount_cents"]
    booking_month = tx["booking_date"][:7]
    start = _start_month(ctx, member, booking_month)
    if ctx.check_start and booking_month < ctx.check_start:
        return _Plan([], outside=True)
    part = _part_paid_month(member, tx, start, booking_month)
    if part is not None:
        return _Plan([(part, amount)])  # vervollständigt (oder ergänzt) den Monat

    allocs: dict[str, int] = {}
    remaining, full, first_shortfall = amount, True, None
    for month in _order(booking_month, start):
        if remaining == 0:
            break
        shortfall = member.expected(month) - member.paid_without(month, tx["id"])
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
    elif len(allocs) > MAX_AUTO_MONTHS:
        reason = REASON_TOO_MANY
    if remaining:
        last = max(allocs) if allocs else booking_month
        allocs[last] = allocs.get(last, 0) + remaining
    return _Plan(sorted(allocs.items()), reason=reason)


def _has_confirmed(conn, tx_id: int) -> bool:
    return conn.execute(
        "select 1 from allocation where tx_id=? and status='confirmed'", (tx_id,)
    ).fetchone() is not None


def _has_manual(conn, tx_id: int) -> bool:
    return conn.execute(
        "select 1 from allocation where tx_id=? and source='manual'", (tx_id,)
    ).fetchone() is not None


def _insert(conn, tx, allocs, status: str, source: str) -> list[int]:
    ids = []
    for m, c in allocs:
        ids.append(conn.execute(
            "insert into allocation(tx_id, member_id, month, cents, status, source)"
            " values(?,?,?,?,?,?)", (tx["id"], tx["member_id"], m, c, status, source)).lastrowid)
    return ids


class _Run(_Ctx):
    """Zwischenspeicher für _passes: dazu bestätigte Buchungen und Vorschlagszeilen."""

    def __init__(self, conn, ids):
        super().__init__(conn)
        wanted = set(ids)
        self.confirmed: set[int] = set()
        self.proposed: dict[int, list[int]] = {}
        for r in conn.execute("select id, tx_id, status from allocation"):
            if r["tx_id"] not in wanted:
                continue
            if r["status"] == "confirmed":
                self.confirmed.add(r["tx_id"])
            else:
                self.proposed.setdefault(r["tx_id"], []).append(r["id"])


def propose_allocations(conn, tx_id: int, *, commit: bool = True,
                        auto: bool = True, ctx: _Run | None = None) -> list[tuple[str, int]]:
    """Verteilung berechnen und speichern. Eindeutiges wird mit auto=True bestätigt.

    auto=False speichert immer einen Vorschlag des Nutzers (source='manual', für
    Rückgängig, Verteilung aufheben und Neu vorschlagen). Gibt die gespeicherte
    Verteilung zurück, leer vor dem Prüfungsstart. ctx: Zwischenspeicher von
    _passes (gleiches Ergebnis, weniger Abfragen).
    """
    tx = _incoming_tx(conn, tx_id)
    if ctx is None:
        if _has_confirmed(conn, tx_id):
            return _stored(conn, tx_id)  # Bestätigtes bleibt unangetastet
        conn.execute(
            "delete from allocation where tx_id=? and status='proposed'", (tx_id,)
        )
    else:
        if tx_id in ctx.confirmed:
            return _stored(conn, tx_id)
        old = ctx.proposed.pop(tx_id, [])
        for i in range(0, len(old), 500):
            chunk = old[i:i + 500]
            conn.execute(f"delete from allocation where id in ({','.join('?' * len(chunk))})",
                         chunk)
    plan = _compute(conn, tx, ctx)
    if auto and plan.clean:
        _insert(conn, tx, plan.allocs, "confirmed", "auto")
        if ctx is not None:
            ctx.confirmed.add(tx_id)
            ctx.member(tx["member_id"]).add(tx_id, plan.allocs)
    else:
        rows = _insert(conn, tx, plan.allocs, "proposed", "auto" if auto else "manual")
        if ctx is not None and rows:
            ctx.proposed[tx_id] = rows
    if commit:
        conn.commit()
    return plan.allocs


def _snapshot(conn, ids) -> dict[int, list[tuple]]:
    """Zeilen je Buchung in einer Abfrage."""
    out: dict[int, list[tuple]] = {t: [] for t in ids}
    for r in conn.execute("select tx_id, month, cents, status, source from allocation"
                          " order by tx_id, month, id"):
        if r["tx_id"] in out:
            out[r["tx_id"]].append((r["month"], r["cents"], r["status"], r["source"]))
    return out


def _passes(conn, ids: list[int]) -> None:
    """Abgeleitete Buchungen chronologisch berechnen, bis ein Lauf nichts mehr bestätigt.

    Eine spätere Bestätigung kann einen früheren Vorschlag eindeutig machen; der
    nächste Lauf holt das nach. Höchstens len(ids) + 1 Läufe: jeder Lauf außer
    dem letzten bestätigt mindestens eine Buchung, die danach fest bleibt.
    Ein Zwischenspeicher je Aufruf hält Soll und Bezahltes im Speicher.
    """
    run = _Run(conn, ids)
    for _ in range(len(ids) + 1):
        new = 0
        for tx_id in ids:
            if tx_id in run.confirmed:
                continue
            propose_allocations(conn, tx_id, commit=False, auto=True, ctx=run)
            new += tx_id in run.confirmed
        if not new:
            return


def _chronological(conn, where: str, params=()) -> list[int]:
    manual = {r[0] for r in conn.execute(
        "select distinct tx_id from allocation where source='manual'")}
    return [r["id"] for r in conn.execute(
        'select t.id from "transaction" t where t.amount_cents > 0 and t.member_id is not null'
        f" and {where} order by t.booking_date, t.id", params) if r["id"] not in manual]


def _delete_rows(conn, tx_ids) -> None:
    ids = list(tx_ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        conn.execute(f"delete from allocation where tx_id in ({','.join('?' * len(chunk))})",
                     chunk)


def rebuild_member(conn, member_id: int, exclude: int | None = None) -> None:
    """Alle abgeleiteten Verteilungen eines Mitglieds neu berechnen (kanonisch).

    Ergibt für dieses Mitglied dasselbe wie redistribute_all. Ohne commit (läuft
    in der Transaktion des Aufrufers). Zeilen mit source='manual' bleiben
    unangetastet; exclude ist eine Buchung, die der Aufrufer selbst behandelt.

    Verworfen: nur ab der geänderten Zahlung neu rechnen. Frühere Vorschläge
    können durch spätere Bestätigungen eindeutig werden, und eine Bestätigung
    des Nutzers in einem früheren Monat muss auch frühere automatische
    Zahlungen verschieben, sonst wäre ein Monat doppelt bezahlt.
    """
    ids = [t for t in _chronological(conn, "t.member_id=?", (member_id,)) if t != exclude]
    _delete_rows(conn, ids)
    _passes(conn, ids)


@dataclass(frozen=True)
class AutoResult:
    total: int      # Buchungen mit Ergebnis (verteilt, zur Prüfung, Vorschlag entfernt)
    confirmed: int  # automatisch verteilt
    review: int     # bleiben zur Prüfung
    outside: int    # Vorschlag entfernt, weil vor dem Prüfungsstart


def auto_distribute_pending(conn) -> AutoResult:
    """Offene automatische Vorschläge und Buchungen ohne Verteilung neu berechnen.

    Alles oder nichts in einem Commit, bis zum Fixpunkt (ein zweiter Aufruf ändert
    nichts). Bestätigtes und Zeilen mit source='manual' bleiben unangetastet.
    Buchungen vor dem Prüfungsstart, die schon keine Zeilen hatten, zählen nicht.
    """
    confirmed_tx = {r[0] for r in conn.execute(
        "select distinct tx_id from allocation where status='confirmed'")}
    ids = [t for t in _chronological(conn, "1=1") if t not in confirmed_tx]
    try:
        before = _snapshot(conn, ids)
        _passes(conn, ids)
        after = _snapshot(conn, ids)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    confirmed = sum(1 for t in ids if after[t] and after[t][0][2] == "confirmed")
    review = sum(1 for t in ids if after[t] and after[t][0][2] == "proposed")
    outside = sum(1 for t in ids if before[t] and not after[t])
    return AutoResult(confirmed + review + outside, confirmed, review, outside)


def redistribute_all(conn, *, commit: bool = True) -> tuple[int, int]:
    """Alle abgeleiteten Verteilungen neu berechnen (z. B. nach neuem Prüfungsstart).

    Löscht alle Zeilen der Buchungen ohne Nutzerentscheidung (nur source='auto')
    und berechnet sie chronologisch bis zum Fixpunkt neu. Alles oder nichts in
    einem Commit; die Schreibsperre bleibt bis dahin gehalten (Zwischen-Commits
    würden das Alles-oder-nichts aufheben). Gibt (geändert, jetzt vor dem
    Prüfungsstart) zurück: geändert zählt nur Buchungen, deren Zeilen sich
    wirklich geändert haben. commit=False: in der Transaktion des Aufrufers
    (Import, #7), ohne commit und ohne rollback.
    """
    if not commit:
        return _redistribute(conn)
    try:
        result = _redistribute(conn)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return result


def _redistribute(conn) -> tuple[int, int]:
    """Wie redistribute_all, aber ohne commit (für Änderungen im selben Commit)."""
    ids = _chronological(conn, "1=1")
    before = _snapshot(conn, ids)
    _delete_rows(conn, ids)
    _passes(conn, ids)
    after = _snapshot(conn, ids)
    changed = sum(1 for t in ids if after[t] and after[t] != before[t])
    outside = sum(1 for t in ids if before[t] and not after[t])
    return changed, outside


def set_check_start_redistribute(conn, month: str) -> tuple[int, int]:
    """Prüfungsstart speichern und alles Abgeleitete neu verteilen, in einem Commit."""
    if not _valid_month(month):
        raise ValueError("Prüfungsstart muss im Format JJJJ-MM sein")
    try:
        conn.execute("insert into settings(key, value) values(?,?)"
                     " on conflict(key) do update set value=excluded.value",
                     (CHECK_START_KEY, month))
        result = _redistribute(conn)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return result


def auto_snapshot(conn, member_id: int | None = None) -> dict[int, list[tuple]]:
    """Zeilen je Zahlungseingang (eines Mitglieds oder aller). Nur lesend.

    Mit count_changed zählen die Oberflächen ehrlich, wie viele Zahlungen eine
    Aktion neu verteilt hat.
    """
    sql = 'select id from "transaction" where amount_cents > 0 and member_id is not null'
    params: tuple = ()
    if member_id is not None:
        sql += " and member_id=?"
        params = (member_id,)
    return _snapshot(conn, [r[0] for r in conn.execute(sql, params)])


def count_changed(before: dict, after: dict, exclude: int | None = None) -> int:
    """Zahl der Buchungen, deren Zeilen sich geändert haben (ohne exclude)."""
    return sum(1 for t in set(before) | set(after)
               if t != exclude and before.get(t, []) != after.get(t, []))


def repropose_pending(conn) -> int:
    """Wie redistribute_all, gibt nur die Zahl geänderter Buchungen zurück."""
    return redistribute_all(conn)[0]


def review_reason(conn, tx_id: int) -> str | None:
    """Kurzer Grund, warum ein Vorschlag geprüft werden muss. Nur lesend.

    None, wenn die Buchung keinen Vorschlag hat.
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
    if plan.outside:
        return REASON_OUTSIDE
    return plan.reason or REASON_STALE


def _kind(booking_month: str, allocs, expected: int) -> str:
    if len(allocs) == 1 and allocs[0][1] < expected:
        return "Teilzahlung"
    months = [m for m, _ in allocs]
    if any(m < booking_month for m in months):
        return "Rückstand"
    if any(m > booking_month for m in months):
        return "Vorauszahlung"
    return "Normal"


_AUTO_WHERE = (" where exists(select 1 from allocation a where a.tx_id=t.id"
               " and a.status='confirmed' and a.source='auto')")


def auto_count(conn) -> int:
    """Zahl automatisch bestätigter Buchungen. Nur lesend."""
    return conn.execute('select count(*) from "transaction" t' + _AUTO_WHERE).fetchone()[0]


def auto_rows(conn, limit: int = 100, offset: int = 0) -> list[dict]:
    """Automatisch bestätigte Buchungen, neueste zuerst. Nur lesend."""
    txs = conn.execute(
        "select t.id, t.booking_date, t.amount_cents, t.cp_name, t.member_id,"
        ' m.name as member_name from "transaction" t join member m on m.id = t.member_id'
        + _AUTO_WHERE + " order by t.booking_date desc, t.id desc limit ? offset ?",
        (int(limit), int(offset)),
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
    member_id = conn.execute('select member_id from "transaction" where id=?',
                             (tx_id,)).fetchone()[0]
    try:
        conn.execute("delete from allocation where tx_id=?", (tx_id,))
        rebuild_member(conn, member_id, exclude=tx_id)  # spätere Zahlungen folgen
        propose_allocations(conn, tx_id, commit=False, auto=False)
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def distribution_counts(conn, tx_ids) -> tuple[int, int, int]:
    """(automatisch verteilt, zur Prüfung, ohne Verteilung) unter den Buchungen.

    "Ohne Verteilung": Eingang mit Mitglied ohne Zeilen (vor dem Prüfungsstart).
    Nur lesend, in Blöcken abgefragt (Grenze der SQL-Parameter).
    """
    ids = sorted(set(tx_ids))
    auto = review = outside = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
            "select t.id,"
            " exists(select 1 from allocation a where a.tx_id=t.id and a.status='proposed') as p,"
            " exists(select 1 from allocation a where a.tx_id=t.id and a.status='confirmed'"
            " and a.source='auto') as c,"
            " exists(select 1 from allocation a where a.tx_id=t.id) as any_row"
            f' from "transaction" t where t.id in ({marks})'
            " and t.amount_cents > 0 and t.member_id is not null", chunk):
            if r["p"]:
                review += 1
            elif r["c"]:
                auto += 1
            elif not r["any_row"]:
                outside += 1
    return auto, review, outside


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
    try:
        conn.execute("delete from allocation where tx_id=?", (tx_id,))
        conn.executemany(
            "insert into allocation(tx_id, member_id, month, cents, status, source)"
            " values(?,?,?,?,'confirmed','manual')",
            [(tx_id, tx["member_id"], m, c) for m, c in allocs],
        )
        rebuild_member(conn, tx["member_id"])  # automatische Zahlungen folgen
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def clear_allocations(conn, tx_id: int) -> int:
    """Hebt die Verteilung einer Buchung auf: daraus wird ein Vorschlag des Nutzers.

    Löscht alle Zeilen (proposed und confirmed) und legt im selben Commit einen
    Vorschlag mit source='manual' an, den automatische Läufe nie bestätigen. Vor
    dem Prüfungsstart oder ohne Mitglied entstehen keine Zeilen. Die Sperre in
    assign_member greift danach nicht mehr (nur Bestätigtes sperrt). Gibt die
    Zahl gelöschter Zeilen zurück.
    """
    tx = conn.execute(
        'select amount_cents, member_id from "transaction" where id=?', (tx_id,)
    ).fetchone()
    if tx is None:
        raise ValueError(f"unbekannte Buchung {tx_id}")
    try:
        n = conn.execute("delete from allocation where tx_id=?", (tx_id,)).rowcount
        if tx["member_id"] is not None and tx["amount_cents"] > 0:
            rebuild_member(conn, tx["member_id"], exclude=tx_id)
            propose_allocations(conn, tx_id, commit=False, auto=False)
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
