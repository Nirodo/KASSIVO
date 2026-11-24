import pytest

from kassivo.categories import add_rule, apply_rules, create_category, set_category


def _tx(conn, amount, purpose=None, cp_name=None, cp_iban=None, n=[0]):
    n[0] += 1
    conn.execute("insert or ignore into account(id, iban) values(1, 'DE00TEST')")
    cur = conn.execute(
        'insert into "transaction"(account_id, booking_date, amount_cents, cp_name,'
        " cp_iban, purpose, dedupe_key) values(1,'2026-01-01',?,?,?,?,?)",
        (amount, cp_name, cp_iban, purpose, f"k{n[0]}"),
    )
    return cur.lastrowid


def _cat(conn, tx_id):
    return conn.execute(
        'select category_id from "transaction" where id=?', (tx_id,)
    ).fetchone()[0]


def test_rule_matches_case_insensitive(conn):
    c = create_category(conn, "Miete", "out")
    add_rule(conn, c, "purpose", "miete")
    t = _tx(conn, -5000, purpose="MIETE Halle Nov")
    assert apply_rules(conn) == 1
    assert _cat(conn, t) == c


def test_umlaut_casefold(conn):
    c = create_category(conn, "Müller", "both")
    add_rule(conn, c, "cp_name", "MÜLLER")
    t = _tx(conn, 100, cp_name="Hans müller")
    apply_rules(conn)
    assert _cat(conn, t) == c


def test_priority_lowest_wins(conn):
    a = create_category(conn, "A", "out")
    b = create_category(conn, "B", "out")
    add_rule(conn, a, "purpose", "halle", priority=50)
    add_rule(conn, b, "purpose", "halle", priority=10)
    t = _tx(conn, -100, purpose="Halle")
    apply_rules(conn)
    assert _cat(conn, t) == b


def test_priority_tie_lowest_rule_id(conn):
    a = create_category(conn, "A", "out")
    b = create_category(conn, "B", "out")
    add_rule(conn, a, "purpose", "x")
    add_rule(conn, b, "purpose", "x")
    t = _tx(conn, -100, purpose="x")
    apply_rules(conn)
    assert _cat(conn, t) == a


def test_manual_kept_without_overwrite(conn):
    a = create_category(conn, "A", "out")
    m = create_category(conn, "M", "out")
    add_rule(conn, a, "purpose", "halle")
    t = _tx(conn, -100, purpose="Halle")
    set_category(conn, t, m)
    assert apply_rules(conn) == 0
    assert _cat(conn, t) == m
    assert apply_rules(conn, overwrite=True) == 1
    assert _cat(conn, t) == a
    assert apply_rules(conn, overwrite=True) == 0


def test_amount_limits_inclusive_abs(conn):
    c = create_category(conn, "Fee", "both")
    add_rule(conn, c, "purpose", "x", min_cents=1000, max_cents=2000)
    t1 = _tx(conn, 1700, purpose="x")
    t2 = _tx(conn, 2500, purpose="x")
    t3 = _tx(conn, -2000, purpose="x")
    t4 = _tx(conn, 999, purpose="x")
    apply_rules(conn)
    assert _cat(conn, t1) == c
    assert _cat(conn, t2) is None
    assert _cat(conn, t3) == c
    assert _cat(conn, t4) is None


def test_none_field_no_crash(conn):
    c = create_category(conn, "N", "both")
    add_rule(conn, c, "cp_name", "x")
    t = _tx(conn, 100, cp_name=None)
    assert apply_rules(conn) == 0
    assert _cat(conn, t) is None


def test_kind_mismatch_skipped(conn):
    cin = create_category(conn, "In", "in")
    add_rule(conn, cin, "purpose", "x")
    t = _tx(conn, -100, purpose="x")
    assert apply_rules(conn) == 0
    assert _cat(conn, t) is None


def test_iban_ignores_spaces_and_case(conn):
    c = create_category(conn, "I", "both")
    add_rule(conn, c, "cp_iban", "de89 3704")
    t = _tx(conn, 100, cp_iban="DE89370400440532013000")
    apply_rules(conn)
    assert _cat(conn, t) == c


def test_tx_ids_subset(conn):
    c = create_category(conn, "S", "both")
    add_rule(conn, c, "purpose", "x")
    t1 = _tx(conn, 100, purpose="x")
    t2 = _tx(conn, 100, purpose="x")
    assert apply_rules(conn, [t1]) == 1
    assert _cat(conn, t2) is None


def test_invalid_inputs(conn):
    c = create_category(conn, "V", "both")
    with pytest.raises(ValueError):
        create_category(conn, "Bad", "x")
    with pytest.raises(ValueError):
        add_rule(conn, c, "amount_cents; drop", "x")
    t = _tx(conn, 100)
    with pytest.raises(ValueError):
        set_category(conn, 9999, c)
    with pytest.raises(ValueError):
        set_category(conn, t, 9999)
    set_category(conn, t, c)
    set_category(conn, t, None)
    assert _cat(conn, t) is None


@pytest.mark.parametrize("name", ["Ohne Kategorie", "ohne kategorie", "  OHNE KATEGORIE "])
def test_reserved_name_rejected(conn, name):
    with pytest.raises(ValueError):
        create_category(conn, name, "both")


def test_duplicate_name_is_value_error(conn):
    create_category(conn, "Doppelt", "both")
    with pytest.raises(ValueError):
        create_category(conn, "Doppelt", "in")
    with pytest.raises(ValueError):
        create_category(conn, "beiträge", "in")  # Seed "Beiträge", Groß/klein egal
