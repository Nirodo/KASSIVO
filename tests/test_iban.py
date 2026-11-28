from kassivo.iban import normalize_iban


def test_normalize_iban():
    assert normalize_iban(" de00 1111\t2222 ") == "DE0011112222"
    assert normalize_iban(None) == ""
    assert normalize_iban("  ") == ""


def test_mask_iban_lives_in_iban_module():
    from kassivo import ingest
    from kassivo.iban import mask_iban
    assert mask_iban("DE00111122223333444455") == "DE00…4455"
    assert mask_iban("DE001") == "…"
    assert ingest.mask_iban is mask_iban
