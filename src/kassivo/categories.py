"""Regeln auf Konten (Schema 4). Beträge sind int-Cent.

Der Modulname bleibt bis Schritt 3. Eine Regel zeigt auf ein Ertrags- oder Aufwandskonto
und bucht über ledger.set_counter_account; Kategorien gibt es ab Schema 4 nicht mehr.
Keine Funktion ruft commit() (Aufrufer).
"""
from kassivo import ledger

# feste Zuordnung Regelfeld -> Spalte; nie SQL aus dem Feldnamen bauen
FIELD_COLUMNS = {"purpose": "purpose", "cp_name": "cp_name", "cp_iban": "cp_iban"}
# Richtung aus der Kontoart: Ertrag nur Eingänge, Aufwand nur Ausgänge
_DIRECTION = {"income": "in", "expense": "out"}


def add_rule(conn, account_id: int, field: str, contains: str, *, priority: int = 100,
             min_cents: int | None = None, max_cents: int | None = None) -> int:
    """Regel auf ein sichtbares Ertrags- oder Aufwandskonto. Sonst ValueError."""
    if field not in FIELD_COLUMNS:
        raise ValueError("Unbekanntes Feld für die Regel.")
    if not contains:
        raise ValueError("Der Suchtext darf nicht leer sein.")
    row = conn.execute("select kind, hidden from ledger_account where id=?",
                       (account_id,)).fetchone()
    if row is None:
        raise ValueError("Das Konto gibt es nicht (mehr).")
    if row["kind"] not in _DIRECTION:
        raise ValueError("Regeln buchen nur auf Ertrags- oder Aufwandskonten.")
    if row["hidden"]:
        raise ValueError("Auf ein ausgeblendetes Konto wird keine Regel gelegt.")
    return conn.execute(
        "insert into posting_rule(account_id, field, contains, priority, min_cents, max_cents)"
        " values(?,?,?,?,?,?)",
        (account_id, field, contains, priority, min_cents, max_cents)).lastrowid


def _norm(field: str, text: str) -> str:
    if field == "cp_iban":
        text = text.replace(" ", "")
    return text.casefold()


def _matches(rule, tx) -> bool:
    kind = rule["kind"]
    amount = tx["amount_cents"]
    if kind == "in" and amount <= 0:
        return False
    if kind == "out" and amount >= 0:
        return False
    size = abs(amount)
    if rule["min_cents"] is not None and size < rule["min_cents"]:
        return False
    if rule["max_cents"] is not None and size > rule["max_cents"]:
        return False
    field = rule["field"]
    value = tx[FIELD_COLUMNS[field]]
    if value is None:
        return False
    return _norm(field, rule["contains"]) in _norm(field, value)


def apply_rules(conn, tx_ids: list[int] | None = None, overwrite: bool = False) -> int:
    """Erste passende Regel (priority, id) bucht über ledger.set_counter_account.

    Ertragskonto nur bei Eingang, Aufwandskonto nur bei Ausgang. Regeln auf ausgeblendeten
    Konten ruhen. Ohne overwrite nur ungebuchte Umsätze. Umsätze in einer abgeschlossenen
    Periode bleiben unberührt. Rückgabe: Zahl neuer Buchungen (gleiches Konto zählt nicht).
    """
    rules = [
        dict(r, kind=_DIRECTION[r["kind"]]) for r in conn.execute(
            "select r.*, a.kind from posting_rule r join ledger_account a on a.id = r.account_id"
            " where a.hidden = 0 and a.kind in ('income', 'expense')"
            " order by r.priority, r.id")
        if r["field"] in FIELD_COLUMNS]
    if not rules:
        return 0
    closed = conn.execute("select start_date, end_date from period"
                          " where status = 'closed'").fetchall()
    txs = conn.execute(
        "select t.id, t.booking_date, t.amount_cents, t.purpose, t.cp_name, t.cp_iban,"
        f' {ledger.BOOKED} as booked from "transaction" t').fetchall()
    wanted = None if tx_ids is None else set(tx_ids)
    changed = 0
    for tx in txs:
        if wanted is not None and tx["id"] not in wanted:
            continue
        if tx["booked"] and not overwrite:
            continue
        if any(s <= tx["booking_date"] <= e for s, e in closed):
            continue
        for rule in rules:
            if _matches(rule, tx):
                before = ledger.valid_posting_id(conn, tx["id"])
                after = ledger.set_counter_account(conn, tx["id"], rule["account_id"],
                                                   source="rule")
                changed += after != before
                break
    return changed
