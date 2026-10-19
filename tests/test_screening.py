from datetime import datetime

from aml_agent.screening import jaro_winkler, name_similarity, normalize, screen_case


def test_jaro_winkler_reference_values():
    assert jaro_winkler("MARTHA", "MARHTA") == approx_eq(0.9611)
    assert jaro_winkler("DIXON", "DICKSONX") == approx_eq(0.8133)
    assert jaro_winkler("ABC", "ABC") == 1.0
    assert jaro_winkler("ABC", "") == 0.0


def approx_eq(x, tol=1e-3):
    class _A:
        def __eq__(self, other):
            return abs(other - x) < tol
    return _A()


def test_normalize_handles_suffixes_and_abbreviations():
    assert normalize("Zarovel Shpg & Trdg FZE") == ["ZAROVEL", "SHIPPING", "TRADING"]
    assert normalize("HARTMAN, Rebekka") == ["HARTMAN", "REBEKKA"]


def test_name_similarity_word_order_and_missing_middle_name():
    assert name_similarity("Kesimir Voranth", "VORANTH, Kesimir Dalovic") >= 0.88
    assert name_similarity("Smith", "SMITH, John Paul") < 0.88


def test_generic_business_words_do_not_create_matches():
    # "Holdings" alone must not make two different companies look alike.
    assert name_similarity("NGF Holdings Ltd", "Kalvaro Holdings Ltd") < 0.88


def test_true_entity_match_is_strong(store):
    result = screen_case(store, "C103", datetime(2026, 10, 10, 23, 59))
    hit = next(h for h in result["hits"] if h["sanctions_id"] == "SAN-0041")
    assert hit["strength"] == "STRONG"
    assert hit["party_role"] == "counterparty"
    assert result["potential_true_match"] is True


def test_customer_false_positive_is_weak(store):
    result = screen_case(store, "C104", datetime(2026, 10, 12, 23, 59))
    hit = next(h for h in result["hits"] if h["sanctions_id"] == "SAN-0107")
    assert hit["strength"] == "WEAK"
    assert hit["secondary_identifiers"] == {"date_of_birth_year": "MISMATCH", "nationality": "MISMATCH"}
    assert result["potential_true_match"] is False


def test_no_spurious_hits_on_ordinary_names(store):
    for cid, day in [("C101", 9), ("C105", 8), ("C109", 10), ("C113", 13)]:
        assert screen_case(store, cid, datetime(2026, 10, day, 23, 59))["hits"] == []
