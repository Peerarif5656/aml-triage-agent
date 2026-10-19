import pytest

from aml_agent.narrative import POLICY_QUERIES
from aml_agent.retrieval import PolicyIndex


@pytest.fixture(scope="module")
def index(store):
    return PolicyIndex(store.policy_text)


def test_all_policy_sections_indexed(index):
    assert len(index.sections) == 15
    assert index.get("POL-SAN-02")["title"].startswith("Potential true sanctions")


@pytest.mark.parametrize("policy_id", sorted(POLICY_QUERIES))
def test_canonical_queries_retrieve_their_section_first(index, policy_id):
    assert index.search(POLICY_QUERIES[policy_id], k=1)[0]["policy_id"] == policy_id


def test_natural_language_queries(index):
    assert index.search("student account receiving money from strangers and sending it to crypto")[0]["policy_id"] == "POL-MULE-01"
    assert index.search("what do I do with a sanctions name hit where the date of birth is different")[0]["policy_id"].startswith("POL-SAN")


def test_unrelated_query_returns_nothing(index):
    assert index.search("zzzz qqqq") == []
