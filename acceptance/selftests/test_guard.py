"""The suite fails a test whose upstream requests found no fixture, unless it expects them."""

import sys
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

SUITE_CONFTEST = Path(__file__).parent.parent / "tests" / "conftest.py"
# A tool of the furnished server that reaches EVS without arguments.
PROBE = """
import pytest

{marker}
def test_probe(mcp):
    assert mcp.call_tool("ncit_release_info", {{}})
"""


@pytest.fixture
def suite(pytester, monkeypatch):
    """A copy of the suite's conftest with no fixtures, run against the furnished server."""

    (pytester.path / "fixtures").mkdir()
    tests = pytester.mkdir("tests")
    (tests / "conftest.py").write_text(SUITE_CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_MODE", "fixture")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_SERVER", f"{sys.executable} -m nci_si_mcp.cli serve")
    return tests


def run(pytester, tests, marker):
    (tests / "test_probe.py").write_text(PROBE.format(marker=marker), encoding="utf-8")
    return pytester.runpytest_subprocess(str(tests), "-p", "no:cacheprovider")


def test_requests_without_a_fixture_fail_the_test_and_are_listed(pytester, suite):
    result = run(pytester, suite, marker="")

    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(
        ["E * Failed: upstream requests without a fixture:", "E * GET evs /api/v1/version {}"]
    )


def test_a_test_marked_unmatched_upstream_may_leave_them(pytester, suite):
    result = run(pytester, suite, marker="@pytest.mark.unmatched_upstream")

    result.assert_outcomes(passed=1)
