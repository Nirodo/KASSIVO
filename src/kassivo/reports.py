"""Jahresauswertung (nur lesend). Beträge in Cent."""

_INC = "coalesce(sum(case when t.amount_cents > 0 then t.amount_cents end), 0)"
_EXP = "coalesce(sum(case when t.amount_cents < 0 then -t.amount_cents end), 0)"


def year_summary(conn, year: int) -> dict:
    if type(year) is not int or not 1 <= year <= 9999:
        raise ValueError("year muss int zwischen 1 und 9999 sein")
    ys = f"{year:04d}"

    inc, exp = conn.execute(
        f'select {_INC}, {_EXP} from "transaction" t'
        " where substr(t.booking_date, 1, 4) = ?", (ys,),
    ).fetchone()

    rows = conn.execute(
        f"select coalesce(c.name, 'Ohne Kategorie') as name,"
        f" {_INC} as inc, {_EXP} as exp"
        ' from "transaction" t left join category c on c.id = t.category_id'
        " where substr(t.booking_date, 1, 4) = ?"
        " group by t.category_id", (ys,),
    ).fetchall()
    by_category = sorted(
        (
            {"name": r["name"], "income_cents": r["inc"], "expense_cents": r["exp"]}
            for r in rows if r["inc"] > 0 or r["exp"] > 0
        ),
        key=lambda c: (-(c["income_cents"] + c["expense_cents"]), c["name"].casefold()),
    )

    months = {
        r["m"]: (r["inc"], r["exp"])
        for r in conn.execute(
            f"select substr(t.booking_date, 1, 7) as m, {_INC} as inc, {_EXP} as exp"
            ' from "transaction" t where substr(t.booking_date, 1, 4) = ?'
            " group by m", (ys,),
        )
    }
    by_month = []
    for i in range(1, 13):
        m = f"{ys}-{i:02d}"
        i_c, e_c = months.get(m, (0, 0))
        by_month.append({"month": m, "income_cents": i_c, "expense_cents": e_c})

    paid = conn.execute(
        "select m.id as id, m.name as name, coalesce(sum(a.cents), 0) as paid"
        " from allocation a join member m on m.id = a.member_id"
        " where a.status = 'confirmed' and substr(a.month, 1, 4) = ?"
        " group by m.id", (ys,),
    ).fetchall()
    by_member = [
        {"name": r["name"], "paid_cents": r["paid"]}
        for r in sorted(paid, key=lambda r: (r["name"].casefold(), r["id"]))
        if r["paid"] > 0
    ]

    return {
        "income_cents": inc,
        "expense_cents": exp,
        "balance_cents": inc - exp,
        "by_category": by_category,
        "by_month": by_month,
        "by_member": by_member,
    }
