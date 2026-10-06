"""The session acceptance cases catch re-resolution, overwritten pins and hidden withdrawal."""

import pytest

pytest_plugins = ["pytester"]

SUITE = "tests/test_release_session.py"
STICKY = "test_a_session_keeps_its_first_implicit_release_across_an_explicit_override"
WITHDRAWN = "test_a_withdrawn_session_release_fails_without_switching_to_the_new_release"


def test_session_cases_pass_against_the_compliant_server(outcomes):
    found = outcomes(SUITE)
    assert found == {STICKY: "passed", WITHDRAWN: "passed"}


@pytest.mark.parametrize(
    ("defect", "test"),
    [
        ("session-reresolves", STICKY),
        ("session-overwrite", STICKY),
        ("session-reresolves", WITHDRAWN),
        ("session-withdrawal-empty", WITHDRAWN),
    ],
)
def test_session_cases_catch_their_defects(outcomes, monkeypatch, defect, test):
    monkeypatch.setenv("COMPLIANT_SERVER_DEFECT", defect)
    assert outcomes(f"{SUITE}::{test}") == {test: "failed"}
