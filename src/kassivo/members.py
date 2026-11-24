from datetime import date

from kassivo.fees import _type_id, fee_at
from kassivo.months import add_months, month_of

_FIELDS = {
    "name", "street", "zip", "city", "email", "phone", "iban",
    "eintritt", "austritt", "fee_start",
}


def create_member(
    conn, name: str, type_name: str, eintritt: date, *,
    street="", zip_code="", city="", email="", phone="", iban=None,
    fee_start: str | None = None,
) -> int:
    type_id = _type_id(conn, type_name)
    if fee_start is None:
        fee_start = add_months(month_of(eintritt), 1)
    cur = conn.execute(
        "insert into member(name, street, zip, city, email, phone, iban, "
        "eintritt, fee_start) values(?,?,?,?,?,?,?,?,?)",
        (name, street, zip_code, city, email, phone, iban,
         eintritt.isoformat(), fee_start),
    )
    conn.execute(
        "insert into member_type_history(member_id, type_id, valid_from) "
        "values(?,?,?)",
        (cur.lastrowid, type_id, fee_start),
    )
    conn.commit()
    return cur.lastrowid


def update_member(conn, member_id: int, **fields) -> None:
    if "zip_code" in fields:
        fields["zip"] = fields.pop("zip_code")
    bad = set(fields) - _FIELDS
    if bad:
        raise ValueError(f"Unbekannte Felder: {sorted(bad)}")
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(
        f"update member set {sets} where id=?", (*fields.values(), member_id)
    )
    conn.commit()


def leave_member(conn, member_id: int, austritt: date) -> None:
    conn.execute(
        "update member set austritt=? where id=?",
        (austritt.isoformat(), member_id),
    )
    conn.commit()


def set_member_type(conn, member_id: int, type_name: str, valid_from: str) -> None:
    conn.execute(
        "insert into member_type_history(member_id, type_id, valid_from) "
        "values(?,?,?) on conflict(member_id, valid_from) "
        "do update set type_id=excluded.type_id",
        (member_id, _type_id(conn, type_name), valid_from),
    )
    conn.commit()


def type_at(conn, member_id: int, month: str) -> str | None:
    row = conn.execute(
        "select t.name from member_type_history h "
        "join membership_type t on t.id=h.type_id "
        "where h.member_id=? and h.valid_from<=? "
        "order by h.valid_from desc limit 1",
        (member_id, month),
    ).fetchone()
    return row["name"] if row else None


def list_members(conn, active_only: bool = False) -> list:
    sql = "select * from member"
    if active_only:
        sql += " where austritt is null"
    return conn.execute(sql + " order by name, id").fetchall()


def expected_cents(conn, member_id: int, month: str) -> int:
    m = conn.execute(
        "select austritt, fee_start from member where id=?", (member_id,)
    ).fetchone()
    if m is None:
        return 0
    if m["fee_start"] and month < m["fee_start"]:
        return 0
    if m["austritt"] and month > m["austritt"][:7]:
        return 0
    type_name = type_at(conn, member_id, month)
    return fee_at(conn, type_name, month) if type_name else 0
