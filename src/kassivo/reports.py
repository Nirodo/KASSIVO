"""Jahresauswertung (nur lesend). Beträge in Cent."""

_INC = "coalesce(sum(case when t.amount_cents > 0 then t.amount_cents end), 0)"
_EXP = "coalesce(sum(case when t.amount_cents < 0 then -t.amount_cents end), 0)"


def _sort_groups(groups) -> list[dict]:
    return sorted(
        (g for g in groups if g["income_cents"] > 0 or g["expense_cents"] > 0),
        key=lambda c: (-(c["income_cents"] + c["expense_cents"]), c["name"].casefold()),
    )


def _by_category_4(conn, ys: str) -> list[dict]:
    """Gruppen je Gegenkonto der gültigen Buchung (Schema 4). Je Umsatz und Gegenkonto zählt
    der Teilbetrag aus Sicht der Bank: positiv als Einnahme, negativ als Ausgabe der Gruppe.
    Ungebuchte Umsätze bilden "Ohne Kategorie"."""
    from kassivo import ledger  # spät: ledger importiert periods, periods importiert db
    span = (f"{ys}-01-01", f"{int(ys) + 1:04d}-01-01")
    rows = conn.execute(
        "with part as ("
        "  select t.id as tx,"
        "   case when w.debit_account_id = b.id then w.credit_account_id"
        "        else w.debit_account_id end as counter,"
        "   sum(case when w.debit_account_id = b.id then w.cents else -w.cents end) as s"
        '  from "transaction" t join posting p on p.tx_id = t.id'
        "  join posting_row w on w.posting_id = p.id"
        "  join ledger_account b on b.bank_account_id = t.account_id"
        "  where t.booking_date >= ? and t.booking_date < ?"
        f"   and {ledger.VALID} and b.id in (w.debit_account_id, w.credit_account_id)"
        "  group by t.id, counter)"
        " select a.id, a.name, coalesce(sum(case when part.s > 0 then part.s end), 0) as inc,"
        "  coalesce(sum(case when part.s < 0 then -part.s end), 0) as exp"
        " from part join ledger_account a on a.id = part.counter group by a.id", span,
    ).fetchall()
    groups = [{"name": r["name"], "income_cents": r["inc"], "expense_cents": r["exp"]}
              for r in rows]
    inc, exp = conn.execute(
        f'select {_INC}, {_EXP} from "transaction" t'
        f" where t.booking_date >= ? and t.booking_date < ? and not {ledger.BOOKED}", span,
    ).fetchone()
    groups.append({"name": "Ohne Kategorie", "income_cents": inc, "expense_cents": exp})
    return _sort_groups(groups)


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
