"""The cross-cutting tests pass against a server that meets them, and each fails on its own
defect (compliant_server.py)."""

from pathlib import Path

import pytest
import yaml

pytest_plugins = ["pytester"]

SUITE = Path(__file__).parent.parent / "tests"
CROSS_CUTTING = "tests/test_crosscutting.py"
UNKNOWN_RELEASE = "test_a_release_the_platform_does_not_serve_fails_closed"
LICENCE_ERROR = "test_an_error_carries_no_licence_key"
LICENCE_REACHES = "test_the_licence_key_reaches_the_platform_and_nothing_the_server_returns_or_logs"
BACKOFF = "test_a_rate_limited_request_is_asked_once_more_after_the_wait"
# Each defect of the compliant server, and the cross-cutting test whose every case must fail.
DEFECTS = [
    ("wrong-release", "test_every_item_carries_the_release_requested"),
    ("wrong-terminology", "test_every_item_carries_the_release_requested"),
    ("invalid-result", "test_a_result_validates_against_the_declared_output_schema"),
    ("list-result", "test_a_result_is_an_object"),
    ("no-served-by", "test_every_item_carries_its_provenance"),
    ("bad-timestamp", "test_every_item_carries_its_provenance"),
    ("no-polarity", "test_an_item_reached_by_traversal_says_how"),
    ("nothing-reached", "test_an_item_reached_by_traversal_says_how"),
    ("prefixed-code", "test_codes_are_bare_with_their_terminology_beside_them"),
    ("repeated-request", "test_no_upstream_request_is_repeated_within_a_call"),
    ("asks-nothing", "test_no_upstream_request_is_repeated_within_a_call"),
    ("uncached-result", "test_a_release_pinned_result_may_be_cached"),
    ("private-scope", "test_a_release_pinned_result_may_be_cached"),
    ("unknown-as-empty", UNKNOWN_RELEASE),
    ("content-with-error", UNKNOWN_RELEASE),
    ("accepts-mismatch", "test_content_of_another_release_fails_closed"),
    ("outage-as-empty", "test_an_unavailable_platform_is_an_upstream_error"),
    ("unshaped-error", "test_an_error_validates_against_the_declared_output_schema"),
    ("keyless", LICENCE_REACHES),
    ("logs-key", LICENCE_REACHES),
    ("logs-key", LICENCE_ERROR),
    ("files-key", LICENCE_REACHES),
    ("key-in-text", LICENCE_REACHES),
    ("leaks-key", LICENCE_ERROR),
    ("key-in-meta", LICENCE_ERROR),
    ("gives-up", BACKOFF),
    ("no-backoff", BACKOFF),
]


def test_every_cross_cutting_test_passes_against_a_server_that_meets_them(outcomes):
    passed = outcomes(CROSS_CUTTING)

    assert set(passed.values()) == {"passed"}
    assert {name.partition("[")[0] for name in passed} == {test for _, test in DEFECTS}


def test_only_a_call_without_a_pinned_form_may_answer_an_unknown_release_with_the_mismatch(
    outcomes, monkeypatch
):
    calls = yaml.safe_load((SUITE / "calls.yaml").read_text(encoding="utf-8"))
    unpinned = {name for name, call in calls.items() if call.get("unpinned")}
    monkeypatch.setenv("COMPLIANT_SERVER_DEFECT", "unpinned-mismatch")

    found = outcomes(CROSS_CUTTING, "-k", UNKNOWN_RELEASE)

    passed = {
        case.partition("[")[2].rstrip("]") for case, outcome in found.items() if outcome == "passed"
    }
    assert unpinned
    assert passed == unpinned
    assert set(found.values()) == {"passed", "failed"}


@pytest.mark.parametrize(("defect", "test"), DEFECTS)
def test_each_cross_cutting_test_fails_on_its_own_defect(outcomes, monkeypatch, defect, test):
    monkeypatch.setenv("COMPLIANT_SERVER_DEFECT", defect)

    failing = outcomes(CROSS_CUTTING, "-k", test)

    assert failing
    assert set(failing.values()) == {"failed"}
