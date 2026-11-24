from kassivo.db import connect, get_setting, set_setting


def test_connect_twice_no_duplicate_seed(tmp_path):
    p = tmp_path / "t.db"
    connect(p).close()
    c = connect(p)
    assert c.execute("select count(*) from membership_type").fetchone()[0] == 2
    assert c.execute("select count(*) from fee_rate").fetchone()[0] == 2
    assert c.execute("select count(*) from category").fetchone()[0] == 3
    c.close()


def test_foreign_keys_on(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_seed_values(conn):
    rows = conn.execute(
        "select t.name, r.cents, r.valid_from from fee_rate r "
        "join membership_type t on t.id=r.type_id order by t.name"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("Aktiv", 1700, "1900-01"), ("Passiv", 1000, "1900-01")]
    cats = conn.execute("select name, kind from category order by name").fetchall()
    assert [tuple(r) for r in cats] == [("Beiträge", "in"), ("Hauptverein", "out"), ("Sonstiges", "both")]


def test_settings(conn):
    assert get_setting(conn, "x") is None
    assert get_setting(conn, "x", "d") == "d"
    set_setting(conn, "x", "1")
    set_setting(conn, "x", "2")
    assert get_setting(conn, "x") == "2"
