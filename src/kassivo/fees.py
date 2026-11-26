def _type_id(conn, type_name: str) -> int:
    row = conn.execute(
        "select id from membership_type where name=?", (type_name,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Unbekannte Mitgliedsart: {type_name}")
    return row["id"]


def set_fee(conn, type_name: str, cents: int, valid_from: str) -> None:
    """Beitrag speichern und alle automatischen Verteilungen neu berechnen (ein Commit)."""
    from kassivo import dues  # spät: dues importiert members, members importiert fees
    type_id = _type_id(conn, type_name)
    try:
        conn.execute(
            "insert into fee_rate(type_id, cents, valid_from) values(?,?,?) "
            "on conflict(type_id, valid_from) do update set cents=excluded.cents",
            (type_id, cents, valid_from),
        )
        dues._redistribute(conn)
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def fee_at(conn, type_name: str, month: str) -> int:
    row = conn.execute(
        "select cents from fee_rate where type_id=? and valid_from<=? "
        "order by valid_from desc limit 1",
        (_type_id(conn, type_name), month),
    ).fetchone()
    return row["cents"] if row else 0
