"""Zuordnung von Zahlungseingängen zu Mitgliedern, mit Lernen der Absender-IBAN.

Eine Buchung wird nie geändert, nur member_id und category_id. Alle Prüfungen
laufen vor dem ersten Schreiben: bei ValueError bleibt die DB unverändert.
IBANs erscheinen nie in Fehlermeldungen.
"""

FEE_CATEGORY = "Beiträge"


def normalize_iban(iban: str | None) -> str:
    """Ohne jeden Leerraum, Großbuchstaben. None oder leer ergibt ''."""
    return "".join((iban or "").split()).upper()


def _set_fee_category_if_empty(conn, tx_id: int) -> None:
    # nur wenn noch keine Kategorie gesetzt ist; eine vorhandene bleibt immer
    conn.execute(
        'update "transaction" set category_id=(select id from category where name=?)'
        " where id=? and category_id is null",
        (FEE_CATEGORY, tx_id),
    )


def assign_member(conn, tx_id: int, member_id: int | None, learn: bool = True) -> None:
    tx = conn.execute(
        'select amount_cents, cp_iban, member_id from "transaction" where id=?',
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
    if member_id != tx["member_id"] and conn.execute(
        "select 1 from allocation where tx_id=? and status='confirmed'", (tx_id,)
    ).fetchone():
        raise ValueError(
            f"Buchung {tx_id} hat bestätigte Verteilung: Verteilung zuerst aufheben"
        )

    conn.execute(
        'update "transaction" set member_id=? where id=?', (member_id, tx_id)
    )
    if member_id != tx["member_id"]:
        # Vorschläge gehören zum alten Mitglied und wären jetzt falsch
        conn.execute(
            "delete from allocation where tx_id=? and status='proposed'", (tx_id,)
        )
    if member_id is not None:
        _set_fee_category_if_empty(conn, tx_id)
        iban = normalize_iban(tx["cp_iban"])
        if learn and iban:
            # letzte manuelle Zuordnung gewinnt
            conn.execute(
                "insert into member_iban(iban, member_id) values(?,?)"
                " on conflict(iban) do update set member_id=excluded.member_id",
                (iban, member_id),
            )
    conn.commit()


def _iban_lookup(conn) -> dict[str, int | None]:
    """IBAN -> Mitglied; None heißt mehrdeutig. member_iban hat Vorrang."""
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


def auto_assign(conn, tx_ids: list[int]) -> int:
    wanted = set(tx_ids)
    if not wanted:
        return 0
    lookup = _iban_lookup(conn)
    austritt = {
        r["id"]: r["austritt"]
        for r in conn.execute("select id, austritt from member where austritt is not null")
    }
    assigned = 0
    rows = conn.execute(
        'select id, cp_iban, booking_date from "transaction"'
        " where amount_cents > 0 and member_id is null and cp_iban is not null"
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
            'update "transaction" set member_id=? where id=?', (member_id, tx["id"])
        )
        _set_fee_category_if_empty(conn, tx["id"])
        assigned += 1
    conn.commit()
    return assigned


def unmatched(conn) -> list:
    return conn.execute(
        'select * from "transaction" where amount_cents > 0 and member_id is null'
        " order by booking_date desc, id desc"
    ).fetchall()
