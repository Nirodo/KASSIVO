"""Import-Service. Nur erfundene Daten."""
import zipfile

import pytest

from kassivo import ingest
from kassivo.camt import CamtError
from kassivo.ingest import ImportResult, import_path
from tests.helpers import IBAN, OTHER_IBAN, make_camt, make_zip

DAYS = ["2025-01-06", "2025-01-07", "2025-01-08"]


def day_files(opening=10000):
    """Drei Tagesdateien mit zusammen 5 Buchungen, alle mit Bank-Referenz."""
    plan = {
        DAYS[0]: [dict(amount=1700, name="Max Muster", cp_iban=OTHER_IBAN, purpose="Beitrag", ref="R1"),
                  dict(amount=-500, name="Laden", cp_iban=OTHER_IBAN, purpose="Material", ref="R2")],
        DAYS[1]: [dict(amount=1000, name="Erika Beispiel", cp_iban=OTHER_IBAN, purpose="Beitrag", ref="R3")],
        DAYS[2]: [dict(amount=1, name="Cent", purpose="Centbuchung", ref="R4"),
                  dict(amount=-12345, name="Hauptverein", cp_iban=OTHER_IBAN, purpose="Umlage", ref="R5")],
    }
    files, bal = {}, opening
    for day, entries in plan.items():
        files[f"{day}-camt.xml"] = make_camt(IBAN, bal, entries, day)
        bal += sum(e["amount"] for e in entries)
    return files


def write_zip(tmp_path, files, name="export.zip"):
    p = tmp_path / name
    p.write_bytes(make_zip(files))
    return p


def tx_count(conn):
    return conn.execute('select count(*) from "transaction"').fetchone()[0]


def test_zip_import_then_reimport_is_all_duplicates(conn, tmp_path):
    p = write_zip(tmp_path, day_files())
    r1 = import_path(conn, p)
    assert isinstance(r1, ImportResult)
    assert (r1.new, r1.duplicates, r1.warnings) == (5, 0, [])
    assert len(r1.new_tx_ids) == 5
    r2 = import_path(conn, p)
    assert (r2.new, r2.duplicates, r2.new_tx_ids) == (0, 5, [])
    assert r2.batch_id != r1.batch_id
    assert tx_count(conn) == 5


def test_stored_values_and_batch_row(conn, tmp_path):
    r = import_path(conn, write_zip(tmp_path, day_files()))
    rows = conn.execute(
        'select amount_cents, booking_date, bank_ref, dedupe_key, category_id, member_id, batch_id '
        'from "transaction" order by id').fetchall()
    assert [x["amount_cents"] for x in rows] == [1700, -500, 1000, 1, -12345]
    assert all(type(x["amount_cents"]) is int for x in rows)
    assert rows[0]["booking_date"] == "2025-01-06"
    assert rows[0]["dedupe_key"] == "R1"
    assert all(x["category_id"] is None and x["member_id"] is None for x in rows)
    assert all(x["batch_id"] == r.batch_id for x in rows)
    b = conn.execute("select * from import_batch where id=?", (r.batch_id,)).fetchone()
    assert (b["filename"], b["new_count"], b["dup_count"]) == ("export.zip", 5, 0)
    assert b["imported_at"]
    assert sorted(r.new_tx_ids) == [x[0] for x in conn.execute('select id from "transaction" order by id')]


def test_account_created_with_label_and_kept(conn, tmp_path):
    import_path(conn, write_zip(tmp_path, day_files()), bank_label="Testbank")
    import_path(conn, write_zip(tmp_path, day_files(), "b.zip"), bank_label="Andere")
    rows = conn.execute("select iban, label from account").fetchall()
    assert [(x["iban"], x["label"]) for x in rows] == [(IBAN, "Testbank")]


def test_same_booking_twice_without_ref_stays_two(conn, tmp_path):
    e = dict(amount=1700, name="Max Muster", cp_iban=OTHER_IBAN, purpose="Beitrag", ref=None)
    p = write_zip(tmp_path, {"a.xml": make_camt(IBAN, 0, [e, e], DAYS[0])})
    r1 = import_path(conn, p)
    assert (r1.new, r1.duplicates) == (2, 0)
    keys = [x[0] for x in conn.execute('select dedupe_key from "transaction"')]
    assert len(set(keys)) == 2 and all(len(k) == 64 for k in keys)
    r2 = import_path(conn, p)
    assert (r2.new, r2.duplicates) == (0, 2)
    assert tx_count(conn) == 2


def test_hash_key_depends_on_fields(conn, tmp_path):
    base = dict(amount=1700, cp_iban=OTHER_IBAN, purpose="Beitrag", ref=None)
    variants = [base, {**base, "amount": -1700}, {**base, "purpose": "Beitrag2"},
                {**base, "cp_iban": None, "name": "Bar"}]
    p = write_zip(tmp_path, {"a.xml": make_camt(IBAN, 0, variants, DAYS[0])})
    assert import_path(conn, p).new == 4


def test_same_ref_on_other_account_is_not_duplicate(conn, tmp_path):
    e = [dict(amount=100, purpose="x", ref="R1")]
    p = write_zip(tmp_path, {"a.xml": make_camt(IBAN, 0, e, DAYS[0]),
                             "b.xml": make_camt(OTHER_IBAN, 0, e, DAYS[0])})
    r = import_path(conn, p)
    assert (r.new, r.duplicates) == (2, 0)
    assert conn.execute("select count(*) from account").fetchone()[0] == 2


def test_duplicate_within_one_import_counts_as_duplicate(conn, tmp_path):
    f = make_camt(IBAN, 0, [dict(amount=100, purpose="x", ref="R1")], DAYS[0])
    r = import_path(conn, write_zip(tmp_path, {"a.xml": f, "kopie.xml": f}))
    assert (r.new, r.duplicates) == (1, 1)


def test_broken_file_rolls_back_everything(conn, tmp_path):
    files = {"gut.xml": day_files()[f"{DAYS[0]}-camt.xml"], "kaputt.xml": b"<Document><nope"}
    with pytest.raises(CamtError, match="kaputt.xml"):
        import_path(conn, write_zip(tmp_path, files))
    assert tx_count(conn) == 0
    assert conn.execute("select count(*) from import_batch").fetchone()[0] == 0
    assert conn.execute("select count(*) from account").fetchone()[0] == 0
    assert not conn.in_transaction


def test_error_message_has_no_personal_data(conn, tmp_path):
    files = {"gut.xml": make_camt(IBAN, 0, [dict(amount=1, name="Max Muster", purpose="Geheim")], DAYS[0]),
             "kaputt.xml": make_camt(IBAN, 0, [dict(amount=1, amount_raw="1.234")], DAYS[0])}
    with pytest.raises(CamtError) as ei:
        import_path(conn, write_zip(tmp_path, files))
    msg = str(ei.value)
    assert "kaputt.xml" in msg
    for secret in (IBAN, "Max Muster", "Geheim"):
        assert secret not in msg


def test_empty_zip_raises(conn, tmp_path):
    with pytest.raises(CamtError, match="Keine XML-Dateien gefunden"):
        import_path(conn, write_zip(tmp_path, {}))


def test_zip_without_xml_raises(conn, tmp_path):
    with pytest.raises(CamtError, match="Keine XML-Dateien gefunden"):
        import_path(conn, write_zip(tmp_path, {"liesmich.txt": b"hallo"}))


def test_empty_folder_raises(conn, tmp_path):
    (tmp_path / "notiz.txt").write_text("x")
    with pytest.raises(CamtError, match="Keine XML-Dateien gefunden"):
        import_path(conn, tmp_path)


def test_not_a_zip_raises_camt_error(conn, tmp_path):
    p = tmp_path / "x.zip"
    p.write_bytes(b"kein zip")
    with pytest.raises(CamtError):
        import_path(conn, p)


def test_wrong_closing_balance_gives_one_masked_warning(conn, tmp_path):
    e = [dict(amount=1700, purpose="Beitrag", ref="R1")]
    files = {"a.xml": make_camt(IBAN, 10000, e, DAYS[0], closing=99999),
             "b.xml": make_camt(IBAN, 11700, [dict(amount=5, purpose="y", ref="R2")], DAYS[1])}
    r = import_path(conn, write_zip(tmp_path, files))
    assert r.new == 2
    assert len(r.warnings) == 1
    w = r.warnings[0]
    assert IBAN not in w
    assert f"{IBAN[:4]}…{IBAN[-4:]}" in w
    assert DAYS[0] in w
    assert "Beitrag" not in w


def test_missing_balances_skip_check(conn, tmp_path):
    f = make_camt(IBAN, 0, [dict(amount=5, purpose="y", ref="R1")], DAYS[0], with_balances=False)
    r = import_path(conn, write_zip(tmp_path, {"a.xml": f}))
    assert (r.new, r.warnings) == (1, [])


def test_folder_import_equals_zip_import(conn, tmp_path):
    files = day_files()
    folder = tmp_path / "ordner"
    folder.mkdir()
    for n, d in files.items():
        (folder / n).write_bytes(d)
    (folder / "notiz.txt").write_text("ignorieren")
    rf = import_path(conn, folder)
    assert (rf.new, rf.duplicates, rf.warnings) == (5, 0, [])
    rz = import_path(conn, write_zip(tmp_path, files))
    assert (rz.new, rz.duplicates) == (0, 5)


def test_folder_and_zip_give_same_rows(tmp_path):
    from kassivo.db import connect
    files = day_files()
    folder = tmp_path / "ordner"
    folder.mkdir()
    for n, d in files.items():
        (folder / n).write_bytes(d)
    cols = 'select booking_date, amount_cents, bank_ref, cp_iban, purpose, dedupe_key from "transaction" order by id'
    a, b = connect(":memory:"), connect(":memory:")
    import_path(a, folder)
    import_path(b, write_zip(tmp_path, files))
    assert [tuple(x) for x in a.execute(cols)] == [tuple(x) for x in b.execute(cols)]


def test_single_xml_file(conn, tmp_path):
    p = tmp_path / "tag.XML"
    p.write_bytes(day_files()[f"{DAYS[1]}-camt.xml"])
    r = import_path(conn, p)
    assert (r.new, r.duplicates) == (1, 0)


def test_unbooked_entries_ignored(conn, tmp_path):
    e = [dict(amount=5, purpose="y", ref="R1"), dict(amount=7, purpose="z", ref="R2", status="PDNG")]
    r = import_path(conn, write_zip(tmp_path, {"a.xml": make_camt(IBAN, 0, e, DAYS[0])}))
    assert (r.new, r.warnings) == (1, [])


def test_zip_entry_too_large_raises(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "MAX_XML_BYTES", 100)
    with pytest.raises(CamtError, match="zu groß"):
        import_path(conn, write_zip(tmp_path, day_files()))
    assert tx_count(conn) == 0


def test_too_many_files_raises(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "MAX_FILES", 2)
    with pytest.raises(CamtError, match="zu viele"):
        import_path(conn, write_zip(tmp_path, day_files()))


def test_zip_never_extracted(conn, tmp_path):
    p = write_zip(tmp_path, {"../boese.xml": day_files()[f"{DAYS[0]}-camt.xml"]})
    before = set(tmp_path.parent.iterdir())
    r = import_path(conn, p)
    assert r.new == 2
    assert set(tmp_path.parent.iterdir()) == before
    assert sorted(x.name for x in tmp_path.iterdir()) == ["export.zip"]


def test_zip_directory_entries_ignored(conn, tmp_path):
    p = tmp_path / "d.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("ordner.xml/", b"")
        z.writestr("ordner.xml/tag.xml", day_files()[f"{DAYS[1]}-camt.xml"])
    assert import_path(conn, p).new == 1


def test_error_during_write_rolls_back(conn, tmp_path, monkeypatch):
    real, calls = ingest._account_id, []

    def flaky(*a):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("Abbruch beim Schreiben")
        return real(*a)

    monkeypatch.setattr(ingest, "_account_id", flaky)
    with pytest.raises(RuntimeError):
        import_path(conn, write_zip(tmp_path, day_files()))
    assert tx_count(conn) == 0
    assert conn.execute("select count(*) from import_batch").fetchone()[0] == 0
    assert not conn.in_transaction


def _sizes():
    files = day_files()
    return files, [len(d) for d in files.values()]


def test_zip_declared_total_over_cap_raises(conn, tmp_path, monkeypatch):
    files, sizes = _sizes()
    monkeypatch.setattr(ingest, "MAX_TOTAL_BYTES", sum(sizes) // 2)
    p = write_zip(tmp_path, files)
    opened = []
    real_open = zipfile.ZipFile.open
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda self, *a, **k: opened.append(1) or real_open(self, *a, **k))
    with pytest.raises(CamtError, match="insgesamt zu groß"):
        import_path(conn, p)
    assert opened == []  # Abbruch vor dem Lesen eines Eintrags
    assert tx_count(conn) == 0


def test_zip_entries_pass_each_but_sum_over_cap_raises(conn, tmp_path, monkeypatch):
    files, sizes = _sizes()
    monkeypatch.setattr(ingest, "MAX_XML_BYTES", max(sizes))
    monkeypatch.setattr(ingest, "MAX_TOTAL_BYTES", sum(sizes) - 1)
    with pytest.raises(CamtError, match="insgesamt zu groß") as ei:
        import_path(conn, write_zip(tmp_path, files))
    assert IBAN not in str(ei.value)
    assert tx_count(conn) == 0


def test_folder_sum_over_cap_raises(conn, tmp_path, monkeypatch):
    files, sizes = _sizes()
    folder = tmp_path / "ordner"
    folder.mkdir()
    for n, d in files.items():
        (folder / n).write_bytes(d)
    monkeypatch.setattr(ingest, "MAX_XML_BYTES", max(sizes))
    monkeypatch.setattr(ingest, "MAX_TOTAL_BYTES", sum(sizes) - 1)
    with pytest.raises(CamtError, match="insgesamt zu groß"):
        import_path(conn, folder)
    assert tx_count(conn) == 0


def test_zip_and_folder_at_cap_still_import(conn, tmp_path, monkeypatch):
    files, sizes = _sizes()
    monkeypatch.setattr(ingest, "MAX_XML_BYTES", max(sizes))
    monkeypatch.setattr(ingest, "MAX_TOTAL_BYTES", sum(sizes))
    assert import_path(conn, write_zip(tmp_path, files)).new == 5
    folder = tmp_path / "ordner"
    folder.mkdir()
    for n, d in files.items():
        (folder / n).write_bytes(d)
    assert import_path(conn, folder).duplicates == 5


def _zero_files():
    files = day_files()
    k = f"{DAYS[1]}-camt.xml"
    files[k] = make_camt(IBAN, 10000 + 1700 - 500, [
        dict(amount=0, name="Bankinfo Geheim", ref="Z1"),
        dict(amount=1000, name="Erika Beispiel", cp_iban=OTHER_IBAN, purpose="Beitrag", ref="R3"),
    ], DAYS[1])
    return files


def test_zero_entry_skipped_with_one_masked_warning(conn, tmp_path):
    z = write_zip(tmp_path, _zero_files())
    r = import_path(conn, z)
    assert r.new == 5 and r.duplicates == 0
    assert len(r.warnings) == 1
    w = r.warnings[0]
    assert f"{IBAN[:4]}…{IBAN[-4:]}" in w and IBAN not in w
    assert DAYS[1] in w and "1 Eintrag" in w and "0,00" in w
    assert "Bankinfo" not in w and "Geheim" not in w
    assert conn.execute('select count(*) from "transaction"').fetchone()[0] == 5
    r2 = import_path(conn, z)
    assert (r2.new, r2.duplicates) == (0, 5)
    assert r2.warnings == r.warnings


def test_plural_wording(conn, tmp_path):
    f = make_camt(IBAN, 0, [dict(amount=0, ref="Z1"), dict(amount=0, ref="Z2")], DAYS[0])
    r = import_path(conn, write_zip(tmp_path, {"a.xml": f}))
    assert "2 Einträge" in r.warnings[0]


def test_only_zero_entries_imports_with_warning(conn, tmp_path):
    f = make_camt(IBAN, 500, [dict(amount=0, ref="Z1")], DAYS[0])
    r = import_path(conn, write_zip(tmp_path, {"a.xml": f}))
    assert (r.new, r.duplicates) == (0, 0)
    assert len(r.warnings) == 1 and DAYS[0] in r.warnings[0]


def test_zero_plus_broken_file_writes_nothing(conn, tmp_path):
    files = _zero_files()
    files["x.xml"] = make_camt(IBAN, 0, [dict(amount_raw="-0.01")], DAYS[2])
    with pytest.raises(CamtError):
        import_path(conn, write_zip(tmp_path, files))
    assert conn.execute('select count(*) from "transaction"').fetchone()[0] == 0
    assert conn.execute("select count(*) from import_batch").fetchone()[0] == 0


def test_dedupe_keys_unchanged_by_zero_entry():
    from kassivo.camt import parse_camt052
    from kassivo.ingest import _dedupe_keys
    e = [dict(amount=1700, purpose="Beitrag", ref=None), dict(amount=1700, purpose="Beitrag", ref=None)]
    z = dict(amount=0, purpose="Info", ref=None)
    plain = parse_camt052(make_camt(IBAN, 0, e, DAYS[0]))
    mixed = parse_camt052(make_camt(IBAN, 0, [e[0], z, e[1]], DAYS[0]))
    assert len(plain.entries) == 2 and _dedupe_keys(plain) == _dedupe_keys(mixed)
