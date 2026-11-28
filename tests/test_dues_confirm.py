"""#56: Vorschläge aus der Beitragstabelle bestätigen. Nur erfundene Daten."""
from datetime import date

from kassivo import dues
from kassivo.members import create_member


def _tx(conn, member_id, amount, day="2026-03-05", n=[0]):
    n[0] += 1
    conn.execute("insert or ignore into account(id, iban) values(1, 'DE00TEST')")
    tx = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, member_id,'
        " cp_name, purpose, dedupe_key) values(1,?,?,?,?,?,?)",
        (day, amount, member_id, "Erfunden", "Beitrag", f"c{n[0]}")).lastrowid
    conn.commit()
    if amount > 0 and member_id is not None:
        dues.propose_allocations(conn, tx)
    return tx


def _erika(conn, name="Erika"):
    dues.set_check_start(conn, "2026-01")
    return create_member(conn, name, "Aktiv", date(2026, 1, 15))  # fee_start 2026-02, 17,00


def _rows(conn, tx):
    return [tuple(r) for r in conn.execute(
        "select month, cents, status, source from allocation where tx_id=? order by month", (tx,))]


# --- Signatur ------------------------------------------------------------------

def test_signature_of_proposal(conn):
    m = _erika(conn)
    tx = _tx(conn, m, 2000)  # 17,00 + 3,00: zur Prüfung
    assert _rows(conn, tx) == [("2026-02", 300, "proposed", "auto"),
                               ("2026-03", 1700, "proposed", "auto")]
    assert dues.proposal_signature(conn, tx) == f"{tx}:{m}:2026-02=300,2026-03=1700"


def test_signature_none_when_confirmed_or_without_rows(conn):
    m = _erika(conn)
    tx = _tx(conn, m, 1700)  # eindeutig, automatisch bestätigt
    assert _rows(conn, tx)[0][2] == "confirmed"
    assert dues.proposal_signature(conn, tx) is None
    out = _tx(conn, m, -500)  # Ausgabe
    assert dues.proposal_signature(conn, out) is None
    assert dues.proposal_signature(conn, 999_999) is None


def test_parse_pick():
    assert dues.parse_pick("7:3:2026-02=300,2026-03=1700") == (7, "7:3:2026-02=300,2026-03=1700")
    for bad in ("", "x", "7", ":3:2026-02=1", "7:3:2026-02=1\n", "-7:3:2026-02=1", "7 :3:a",
                "7:" + "a" * 5000):
        assert dues.parse_pick(bad) is None, bad


# --- Einzeln bestätigen ------------------------------------------------------

def test_confirm_proposal_confirms_exactly_shown_rows(conn):
    m = _erika(conn)
    tx = _tx(conn, m, 2000)
    sig = dues.proposal_signature(conn, tx)
    assert dues.confirm_proposal(conn, tx, sig) == "confirmed"
    assert _rows(conn, tx) == [("2026-02", 300, "confirmed", "manual"),
                               ("2026-03", 1700, "confirmed", "manual")]


def test_confirm_proposal_stale_changes_nothing(conn):
    m = _erika(conn)
    tx = _tx(conn, m, 2000)
    before = _rows(conn, tx)
    assert dues.confirm_proposal(conn, tx, f"{tx}:{m}:2026-03=2000") == "stale"
    assert dues.confirm_proposal(conn, tx, f"{tx}:{m + 1}:2026-02=300,2026-03=1700") == "stale"
    assert _rows(conn, tx) == before
    assert not conn.in_transaction


def test_confirm_proposal_twice_is_stale_second_time(conn):
    m = _erika(conn)
    tx = _tx(conn, m, 2000)
    sig = dues.proposal_signature(conn, tx)
    assert dues.confirm_proposal(conn, tx, sig) == "confirmed"
    after = _rows(conn, tx)
    assert dues.confirm_proposal(conn, tx, sig) == "stale"  # doppelt abgeschickt
    assert _rows(conn, tx) == after


def test_confirm_proposal_overpay_only_with_permission(conn):
    m = _erika(conn)
    a = _tx(conn, m, 2000)
    b = _tx(conn, m, 2000, "2026-03-06")  # gleicher Vorschlag, gleiche Monate
    assert dues.proposal_signature(conn, b)[len(str(b)):] == dues.proposal_signature(conn, a)[len(str(a)):]
    dues.clear_allocations(conn, a)  # Vorschläge des Nutzers: nie neu berechnet
    dues.clear_allocations(conn, b)
    assert dues.confirm_proposal(conn, a, dues.proposal_signature(conn, a)) == "confirmed"
    sig_b = dues.proposal_signature(conn, b)
    assert dues.overpaid_months(conn, b) == ["2026-03"]  # Februar: 3,00 + 3,00 passt
    assert dues.confirm_proposal(conn, b, sig_b, allow_overpay=False) == "overpay"
    assert _rows(conn, b)[0][2] == "proposed"
    assert dues.confirm_proposal(conn, b, sig_b, allow_overpay=True) == "confirmed"


def test_confirm_one_cent_proposal(conn):
    m = _erika(conn)
    _tx(conn, m, 1700, "2026-02-03")  # Februar bezahlt
    tx = _tx(conn, m, 1)  # Teilzahlung März: eindeutig
    assert _rows(conn, tx) == [("2026-03", 1, "confirmed", "auto")]
    dues.undo_auto(conn, tx)
    sig = dues.proposal_signature(conn, tx)
    assert sig == f"{tx}:{m}:2026-03=1"
    assert dues.confirm_proposal(conn, tx, sig) == "confirmed"
    assert _rows(conn, tx) == [("2026-03", 1, "confirmed", "manual")]


# --- Sammelbestätigung -----------------------------------------------------------

def test_confirm_proposals_skips_stale_after_rebuild(conn):
    m = _erika(conn)
    a = _tx(conn, m, 2000)
    b = _tx(conn, m, 2000, "2026-03-06")  # beide auf Februar/März vorgeschlagen
    picks = [dues.proposal_signature(conn, b), dues.proposal_signature(conn, a)]
    res = dues.confirm_proposals(conn, picks)
    assert res.confirmed == [a]  # chronologisch zuerst
    assert res.stale == [b]  # nach Neuberechnung anders verteilt: nicht still bestätigt
    paid = {(r[0]): r[1] for r in conn.execute(
        "select month, sum(cents) from allocation where status='confirmed' and member_id=?"
        " group by month", (m,))}
    assert all(v <= 1700 for v in paid.values())


def test_confirm_proposals_never_overpays_a_month(conn):
    m = _erika(conn)
    a = _tx(conn, m, 2000)
    b = _tx(conn, m, 2000, "2026-03-06")
    dues.clear_allocations(conn, a)
    dues.clear_allocations(conn, b)
    picks = [dues.proposal_signature(conn, a), dues.proposal_signature(conn, b)]
    res = dues.confirm_proposals(conn, picks)
    assert res.confirmed == [a] and res.overpay == [b] and res.stale == []
    assert _rows(conn, b)[0][2] == "proposed"


def test_confirm_proposals_ignores_duplicates_and_invalid(conn):
    m = _erika(conn)
    tx = _tx(conn, m, 2000)
    sig = dues.proposal_signature(conn, tx)
    res = dues.confirm_proposals(conn, [sig, sig, "kaputt", f"999999:{m}:2026-02=1"])
    assert res.confirmed == [tx]
    assert res.invalid == 1 and res.stale == [999999]


def test_confirm_proposals_only_picked(conn):
    m = _erika(conn)
    o = _erika(conn, "Otto")
    a = _tx(conn, m, 2000)
    b = _tx(conn, o, 2000)
    res = dues.confirm_proposals(conn, [dues.proposal_signature(conn, b)])
    assert res.confirmed == [b]
    assert _rows(conn, a)[0][2] == "proposed"


# --- Vorschau ---------------------------------------------------------------------

def test_preview_lists_proposals_with_reason_and_conflicts(conn):
    m = _erika(conn)
    a = _tx(conn, m, 2000)
    b = _tx(conn, m, 2000, "2026-03-06")
    c = _tx(conn, m, 2000, "2026-03-07")
    dues.clear_allocations(conn, c)  # vom Nutzer zur Prüfung gestellt
    items = dues.proposal_preview(conn)
    assert [i["tx_id"] for i in items] == [a, b, c]
    first, second, manual = items
    assert first["member_name"] == "Erika" and first["amount_cents"] == 2000
    assert first["allocations"] == [("2026-02", 300), ("2026-03", 1700)]
    assert first["reason"] == dues.REASON_AMOUNT
    assert first["signature"] == dues.proposal_signature(conn, a)
    assert first["selected"] and first["overpaid"] == []
    assert not second["selected"] and second["overpaid"] == ["2026-03"]
    assert manual["reason"] == dues.REASON_MANUAL and not manual["selected"]


def test_preview_empty_and_limit(conn):
    assert dues.proposal_preview(conn) == []
    m = _erika(conn)
    for i in range(3):
        _tx(conn, m, 2000, f"2026-03-0{i + 1}")
    assert len(dues.proposal_preview(conn, limit=2)) == 2


# --- Zelle --------------------------------------------------------------------------

def test_cell_payments_show_all_rows_of_payment(conn):
    m = _erika(conn)
    a = _tx(conn, m, 1700, "2026-02-03")
    b = _tx(conn, m, 2000)  # März voll, 3,00 in den April
    (auto,) = dues.cell_payments(conn, m, "2026-02")
    assert auto["tx_id"] == a and auto["state"] == "auto" and auto["signature"] is None
    assert auto["allocations"] == [("2026-02", 1700, "confirmed", "auto")]
    (prop,) = dues.cell_payments(conn, m, "2026-04")
    assert dues.cell_payments(conn, m, "2026-03") == [prop]
    assert prop["state"] == "proposed" and prop["signature"] == dues.proposal_signature(conn, b)
    assert prop["allocations"] == [("2026-03", 1700, "proposed", "auto"),
                                   ("2026-04", 300, "proposed", "auto")]
    assert prop["reason"] == dues.REASON_AMOUNT and prop["overpaid"] == []
    assert dues.cell_payments(conn, m, "2026-05") == []


def test_cell_payments_overpaid(conn):
    m = _erika(conn)
    a = _tx(conn, m, 2000)
    b = _tx(conn, m, 2000, "2026-03-06")
    dues.clear_allocations(conn, b)
    dues.confirm_allocations(conn, a, [("2026-03", 2000)])
    done, p = dues.cell_payments(conn, m, "2026-03")
    assert done["tx_id"] == a and done["state"] == "manual" and done["overpaid"] == []
    assert p["tx_id"] == b and p["overpaid"] == ["2026-03"]


def test_cell_payments_manual_confirmed(conn):
    m = _erika(conn)
    tx = _tx(conn, m, 2000)
    dues.confirm_allocations(conn, tx, [("2026-03", 2000)])
    (p,) = dues.cell_payments(conn, m, "2026-03")
    assert p["state"] == "manual" and p["signature"] is None and p["reason"] is None
