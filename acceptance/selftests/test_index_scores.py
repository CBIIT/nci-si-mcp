"""The indexed-search acceptance check distinguishes numeric scores from malformed data."""

import runpy
from types import SimpleNamespace

import pytest
from conftest import SUITE


@pytest.fixture(scope="module")
def indexed_search_case():
    return runpy.run_path(str(SUITE / "test_evs.py"))


def assert_index_score(case, score, mode):
    pin = {"terminology": "ncit", "release": "26.09d"}
    entry = {
        "concept": {
            "code": case["CONCEPT"],
            "provenance": {
                "release": {"terminology": "ncit", "identifier": "26.09d"},
                "source": "evs_index",
                "servedBy": "index",
            },
        },
        "score": score,
        "matchedOn": "name",
    }
    result = SimpleNamespace(
        is_error=False, content={"results": [entry], "totalKnown": len(case["INDEX_SET"])}
    )
    tools = SimpleNamespace(call=lambda *_arguments: result)

    def recorded(_path):
        return {"response": {"body": {"name": "Example"}}}

    case["test_index_search_returns_scored_indexed_concepts_and_the_named_one_first_page"](
        tools, pin, recorded, mode
    )


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
@pytest.mark.parametrize("score", [0, -0.5, 1.0])
def test_index_acceptance_accepts_finite_scores_without_imposing_a_range(
    indexed_search_case, mode, score
):
    assert_index_score(indexed_search_case, score, mode)


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
@pytest.mark.parametrize(
    "score", [True, False, "1", None, float("nan"), float("inf"), -float("inf")]
)
def test_index_acceptance_rejects_scores_that_are_not_json_numbers(
    indexed_search_case, mode, score
):
    with pytest.raises(AssertionError):
        assert_index_score(indexed_search_case, score, mode)
