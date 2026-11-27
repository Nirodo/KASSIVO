"""Zuordnung von Zahlungseingängen zu Mitgliedern, mit Lernen der Absender-IBAN.

Eine Buchung wird nie geändert, nur member_id,
category_id und no_member. Alle Prüfungen
laufen vor dem ersten Schreiben: bei ValueError bleibt die DB unverändert.
IBANs erscheinen nie in Fehlermeldungen.

Nach jeder neuen Zuordnung berechnet dues die abgeleiteten Verteilungen
(source='auto') des betroffenen Mitglieds ab dieser Zahlung neu, im selben
Commit wie die Zuordnung (alles oder nichts). Entscheidungen des Nutzers
(source='manual') bleiben unangetastet.
"""
from kassivo import dues
from kassivo.iban import normalize_iban

FEE_CATEGORY = "Beiträge"


def _set_fee_category_if_empty(conn, tx_id: int) -> None:
    # nur wenn noch keine Kategorie gesetzt ist; eine vorhandene bleibt immer
    conn.execute(
        'update "transaction" set category_id=(select id from category where name=?)'
        " where id=? and category_id is null",
        (FEE_CATEGORY, tx_id),
    )


def assign_member(conn, tx_id: int, member_id: int | None, learn: bool = True) -> None:
    tx = conn.execute(
        'select amount_cents, cp_iban, member_id, booking_date from "transaction" where id=?',
        (tx_id,),
    ).fetchone()
    if tx is None:
        raise ValueError(f"unbekannte Buchung {tx_id}")
    if tx["amount_cents"] <= 0:
        raise ValueError(f"Buchung {tx_id} ist kein Zahlungseingang")
    if member_id is not None and not conn.execute(
        "select 1 from member where id=?", (member_id,)
    ).fetchone():
        raise ValueError(f"unbekanntes Mitglied {member_id}")
    # Gesperrt ist nur, was der Nutzer bestätigt hat. Eine automatische Verteilung
    # (source='auto') hängt am alten Mitglied und wird beim Wechsel neu berechnet
    # (#27, sonst blockiert die Vorauszahlung des Geschwisterkinds die Korrektur).
    if member_id != tx["member_id"] and conn.execute(
        "select 1 from allocation where tx_id=? and status='confirmed'"
        " and source='manual'", (tx_id,)
    ).fetchone():
        raise ValueError(
            f"Buchung {tx_id} hat bestätigte Verteilung: Verteilung zuerst aufheben"
        )

    try:
        # "— keins —" des Nutzers ist eine Entscheidung (#6): no_member=1, die
        # automatische Zuordnung lässt die Buchung aus. Ein Mitglied hebt das auf.
        conn.execute(
            'update "transaction" set member_id=?, no_member=? where id=?',
            (member_id, 1 if member_id is None else 0, tx_id),
        )
        changed = member_id != tx["member_id"]
        had_manual = False
        if changed:
            # Vorschläge und automatische Verteilungen gehören zum alten Mitglied
            # und wären jetzt falsch (Bestätigungen des Nutzers sperren oben).
            # Ein Vorschlag des Nutzers (Rückgängig, aufgehoben) bleibt einer.
            had_manual = conn.execute(
                "select 1 from allocation where tx_id=? and source='manual'", (tx_id,)
            ).fetchone() is not None
            conn.execute("delete from allocation where tx_id=?", (tx_id,))
            if tx["member_id"] is not None:
                # die anderen Zahlungen des alten Mitglieds sahen diese Zahlung
                dues.rebuild_member(conn, tx["member_id"])
        if member_id is not None:
            _set_fee_category_if_empty(conn, tx_id)
            iban = normalize_iban(tx["cp_iban"])
            if learn and iban:
                # Maßgeblich ist, was auto_assign heute für die IBAN nähme
                # (gelernt, mehrdeutig oder member.iban): gehört sie schon
                # einem anderen Mitglied oder ist mehrdeutig, wird sie
                # mehrdeutig (NULL), auto_assign überspringt sie.
                # Verworfen: "letzte Zuordnung gewinnt" (Geschwister-Fall
                # ordnete still alle Zahlungen dem zuletzt gewählten Kind zu)
                # und nur gelernte Einträge vergleichen (übersah Geschwister
                # mit derselben Eltern-IBAN in member.iban).
                lookup = _iban_lookup(conn)
                learned = (member_id if iban not in lookup
                           or lookup[iban] == member_id else None)
                conn.execute(
                    "insert into member_iban(iban, member_id) values(?,?)"
                    " on conflict(iban) do update set member_id=excluded.member_id",
                    (iban, learned),
                )
            if changed:
                if had_manual:
                    dues.rebuild_member(conn, member_id, exclude=tx_id)
                    dues.propose_allocations(conn, tx_id, commit=False, auto=False)
                else:
                    # diese und die anderen abgeleiteten Zahlungen des neuen Mitglieds
                    dues.rebuild_member(conn, member_id)
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def _iban_lookup(conn) -> dict[str, int | None]:
    """IBAN -> Mitglied; None heißt mehrdeutig. member_iban hat Vorrang.

    Ein mehrdeutiger Eintrag in member_iban (member_id NULL) überdeckt
    member.iban: kein Rückgriff auf die Stammdaten.
    """
    by_member: dict[str, set[int]] = {}
    for r in conn.execute("select id, iban from member where iban is not null"):
        iban = normalize_iban(r["iban"])
        if iban:
            by_member.setdefault(iban, set()).add(r["id"])
    lookup: dict[str, int | None] = {
        iban: (next(iter(ids)) if len(ids) == 1 else None)
        for iban, ids in by_member.items()
    }
    for r in conn.execute("select iban, member_id from member_iban"):
        iban = normalize_iban(r["iban"])
        if iban:
            lookup[iban] = r["member_id"]
    return lookup


# Ein Eingang ohne Mitglied ist erledigt, sobald er eine andere Kategorie als
# "Beiträge" hat (zum Beispiel Zinsen): er braucht dann kein Mitglied.
_NEEDS_MEMBER = (
    "(category_id is null or category_id in"
    " (select id from category where name = 'Beiträge'))"
)


def iban_is_ambiguous(conn, iban: str | None) -> bool:
    """True, wenn auto_assign die IBAN als mehrdeutig überspringt."""
    iban = normalize_iban(iban)
    lookup = _iban_lookup(conn)
    return bool(iban) and iban in lookup and lookup[iban] is None


def auto_assign(conn, tx_ids: list[int], *, commit: bool = True) -> int:
    """Ordnet Eingänge über gelernte und Stamm-IBANs zu.

    commit=False: läuft in der Transaktion des Aufrufers (Import, #7), ohne
    commit und ohne rollback; den Fehlerfall behandelt der Aufrufer.
    """
    if not commit:
        return _auto_assign(conn, tx_ids)
    try:
        assigned = _auto_assign(conn, tx_ids)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return assigned


def _auto_assign(conn, tx_ids: list[int]) -> int:
    wanted = set(tx_ids)
    if not wanted:
        return 0
    lookup = _iban_lookup(conn)
    austritt = {
        r["id"]: r["austritt"]
        for r in conn.execute("select id, austritt from member where austritt is not null")
    }
    assigned = 0
    touched: set[int] = set()  # Mitglieder mit neuen Zahlungen
    rows = conn.execute(
        'select id, cp_iban, booking_date from "transaction"'
        " where amount_cents > 0 and member_id is null and cp_iban is not null"
        " and no_member = 0 and "+_NEEDS_MEMBER+
        " order by booking_date, id"
    ).fetchall()
    for tx in rows:
        if tx["id"] not in wanted:
            continue
        member_id = lookup.get(normalize_iban(tx["cp_iban"]))
        if member_id is None:  # unbekannt oder mehrdeutig
            continue
        left = austritt.get(member_id)
        if left and tx["booking_date"] > left:
            # ausgetreten: bleibt in "Unklar", kein Rückgriff auf member.iban
            continue
        conn.execute(
            'update "transaction" set member_id=? where id=?',
            (member_id, tx["id"]),
        )
        _set_fee_category_if_empty(conn, tx["id"])
        touched.add(member_id)
        assigned += 1
    # danach je Mitglied einmal chronologisch verteilen
    for member_id in sorted(touched):
        dues.rebuild_member(conn, member_id)
    return assigned


def unmatched(conn) -> list:
    return conn.execute(
        'select * from "transaction" where amount_cents > 0 and member_id is null'
        " and "+_NEEDS_MEMBER+
        " order by booking_date desc, id desc"
    ).fetchall()
