def _type_id(conn, type_name: str) -> int:
    row = conn.execute(
        "select id from membership_type where name=?", (type_name,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Unbekannte Mitgliedsart: {type_name}")
    return row["id"]


def set_fee(conn, type_name: str, cents: int, valid_from: str) -> None:
    conn.execute(
        "insert into fee_rate(type_id, cents, valid_from) values(?,?,?) "
        "on conflict(type_id, valid_from) do update set cents=excluded.cents",
        (_type_id(conn, type_name), cents, valid_from),
    )
    conn.commit()


def fee_at(conn, type_name: str, month: str) -> int:
    row = conn.execute(
        "select cents from fee_rate where type_id=? and valid_from<=? "
        "order by valid_from desc limit 1",
        (_type_id(conn, type_name), month),
    ).fetchone()
    return row["cents"] if row else 0
