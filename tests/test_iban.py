from kassivo.iban import normalize_iban


def test_normalize_iban():
    assert normalize_iban(" de00 1111\t2222 ") == "DE0011112222"
    assert normalize_iban(None) == ""
    assert normalize_iban("  ") == ""
