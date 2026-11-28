"""Kategorien und feste Zuweisungsregeln. Beträge sind int-Cent."""

KINDS = ("in", "out", "both")
# feste Zuordnung Regelfeld -> Spalte; nie SQL aus dem Feldnamen bauen
FIELD_COLUMNS = {"purpose": "purpose", "cp_name": "cp_name", "cp_iban": "cp_iban"}
# Anzeigename für Buchungen ohne Kategorie; darf keine echte Kategorie heißen
RESERVED_NAME = "Ohne Kategorie"


def create_category(conn, name: str, kind: str) -> int:
    if kind not in KINDS:
        raise ValueError(f"kind muss in/out/both sein, nicht {kind!r}")
    key = name.strip().casefold()
    if key == RESERVED_NAME.casefold():
        raise ValueError(f"Der Name {RESERVED_NAME!r} ist reserviert")
    if any(r["name"].strip().casefold() == key for r in conn.execute("select name from category")):
        raise ValueError("Eine Kategorie mit diesem Namen existiert bereits")
    cur = conn.execute("insert into category(name, kind) values(?,?)", (name, kind))
    return cur.lastrowid


def add_rule(
    conn,
    category_id: int,
    field: str,
    contains: str,
    *,
    priority: int = 100,
    min_cents: int | None = None,
    max_cents: int | None = None,
) -> int:
    if field not in FIELD_COLUMNS:
        raise ValueError(f"unbekanntes Feld {field!r}")
    if not contains:
        raise ValueError("contains darf nicht leer sein")
    if not conn.execute("select 1 from category where id=?", (category_id,)).fetchone():
        raise ValueError(f"unbekannte Kategorie {category_id}")
    cur = conn.execute(
        "insert into category_rule(category_id, field, contains, priority,"
        " min_cents, max_cents) values(?,?,?,?,?,?)",
        (category_id, field, contains, priority, min_cents, max_cents),
    )
    return cur.lastrowid


def set_category(conn, tx_id: int, category_id: int | None) -> None:
    if not conn.execute('select 1 from "transaction" where id=?', (tx_id,)).fetchone():
        raise ValueError(f"unbekannte Buchung {tx_id}")
    if category_id is not None and not conn.execute(
        "select 1 from category where id=?", (category_id,)
    ).fetchone():
        raise ValueError(f"unbekannte Kategorie {category_id}")
    conn.execute(
        'update "transaction" set category_id=? where id=?', (category_id, tx_id)
    )


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


def _add_rule_4(conn, account_id: int, field: str, contains: str, *, priority: int = 100,
                min_cents: int | None = None, max_cents: int | None = None) -> int:
    """Regel auf ein sichtbares Ertrags- oder Aufwandskonto (Schema 4)."""
    if field not in FIELD_COLUMNS:
        raise ValueError("Unbekanntes Feld für die Regel.")
    if not contains:
        raise ValueError("Der Suchtext darf nicht leer sein.")
    row = conn.execute("select kind, hidden from ledger_account where id=?",
                       (account_id,)).fetchone()
    if row is None:
        raise ValueError("Das Konto gibt es nicht (mehr).")
    if row["kind"] not in ("income", "expense"):
        raise ValueError("Regeln buchen nur auf Ertrags- oder Aufwandskonten.")
    if row["hidden"]:
        raise ValueError("Auf ein ausgeblendetes Konto wird keine Regel gelegt.")
    return conn.execute(
        "insert into posting_rule(account_id, field, contains, priority, min_cents, max_cents)"
        " values(?,?,?,?,?,?)",
        (account_id, field, contains, priority, min_cents, max_cents)).lastrowid


def _apply_rules_4(conn, tx_ids: list[int] | None = None, overwrite: bool = False) -> int:
    """Erste passende Regel (priority, id) bucht über ledger.set_counter_account (Schema 4).

    Ertragskonto nur bei Eingang, Aufwandskonto nur bei Ausgang. Regeln auf ausgeblendeten
    Konten ruhen. Ohne overwrite nur ungebuchte Umsätze. Umsätze in einer abgeschlossenen
    Periode bleiben unberührt. Rückgabe: Zahl neuer Buchungen (gleiches Konto zählt nicht).
    Ohne commit (Aufrufer)."""
    from kassivo import ledger  # spät: ledger importiert periods, periods importiert db
    rules = [
        dict(r, kind={"income": "in", "expense": "out"}[r["kind"]]) for r in conn.execute(
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


def apply_rules(conn, tx_ids: list[int] | None = None, overwrite: bool = False) -> int:
    rules = conn.execute(
        "select r.*, c.kind from category_rule r join category c on c.id=r.category_id"
        " order by r.priority, r.id"
    ).fetchall()
    rules = [r for r in rules if r["field"] in FIELD_COLUMNS]
    txs = conn.execute(
        'select id, amount_cents, purpose, cp_name, cp_iban, category_id from "transaction"'
    ).fetchall()
    wanted = None if tx_ids is None else set(tx_ids)
    changed = 0
    for tx in txs:
        if wanted is not None and tx["id"] not in wanted:
            continue
        if tx["category_id"] is not None and not overwrite:
            continue
        for rule in rules:
            if _matches(rule, tx):
                if rule["category_id"] != tx["category_id"]:
                    conn.execute(
                        'update "transaction" set category_id=? where id=?',
                        (rule["category_id"], tx["id"]),
                    )
                    changed += 1
                break
    return changed
