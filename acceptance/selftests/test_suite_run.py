"""A run of the suite, end to end, from a copy of the suite's conftest and the furnished server.

These check what only a whole run shows: the missing-fixture guard, a scenario served
to a server process of its own, the tool map, and the per-tool report.
"""

import json
import sys
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

SUITE_CONFTEST = Path(__file__).parent.parent / "tests" / "conftest.py"
# The furnished server's `ncit_release_info` stands in for `resolve_release`.
TOOLMAP = "resolve_release:\n  tool: ncit_release_info\n"
VERSION_REQUEST = {"surface": "evs", "method": "GET", "path": "/api/v1/version"}


def fixture(directory, name, response, **document):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "kind": "crafted",
        "requirement": "self-test",
        "request": VERSION_REQUEST,
    } | document
    path.write_text(json.dumps(document | {"response": response}), encoding="utf-8")


@pytest.fixture
def suite(pytester, monkeypatch):
    """A copy of the suite's conftest, with a tool map and no fixtures yet."""

    fixtures = pytester.mkdir("fixtures")
    (fixtures / "baseline_toolmap.yaml").write_text(TOOLMAP, encoding="utf-8")
    tests = pytester.mkdir("tests")
    (tests / "conftest.py").write_text(SUITE_CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_MODE", "fixture")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_SERVER", f"{sys.executable} -m nci_si_mcp.cli serve")
    return pytester


def run(suite, test, *options):
    (suite.path / "tests" / "test_probe.py").write_text(test, encoding="utf-8")
    return suite.runpytest_subprocess("tests", "-p", "no:cacheprovider", *options)


PROBE = """
import pytest

@pytest.mark.tool("resolve_release")
{marker}
def test_probe(tools):
    assert tools.call("resolve_release").tool == "ncit_release_info"
"""


def test_requests_without_a_fixture_fail_the_test_and_are_listed(suite):
    result = run(suite, PROBE.format(marker=""), "--report=report.json")

    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(
        ["E * Failed: upstream requests without a fixture:", "E * GET evs /api/v1/version {}"]
    )
    report = json.loads((suite.path / "report.json").read_text(encoding="utf-8"))
    assert report["tools"]["resolve_release"]["outcome"] == "NO FIXTURE"


def test_a_test_marked_unmatched_upstream_may_leave_them(suite):
    result = run(suite, PROBE.format(marker="@pytest.mark.unmatched_upstream"))

    result.assert_outcomes(passed=1)


def test_a_scenario_is_served_to_a_server_of_its_own_and_ends_with_the_test(suite):
    fixtures = suite.path / "fixtures"
    fixture(fixtures, "recorded/version.json", {"status": 200, "body": {"version": "ordinary"}})
    fixture(
        fixtures,
        "scenarios/probe/other/version.json",
        {"status": 200, "body": {"version": "scenario"}},
    )
    test = """
import pytest

def version(upstream):
    return [entry["fixture"] for entry in upstream.log() if entry["path"] == "/api/v1/version"]

@pytest.mark.unmatched_upstream
@pytest.mark.scenario("probe/other")
def test_during(tools, server, upstream):
    assert tools is not server
    tools.call("resolve_release")
    assert version(upstream) == ["scenarios/probe/other/version.json"]

@pytest.mark.unmatched_upstream
def test_after(tools, upstream):
    tools.call("resolve_release")
    assert version(upstream) == ["recorded/version.json"]
"""
    result = run(suite, test)

    result.assert_outcomes(passed=2)


def test_the_report_gives_each_required_tool_its_outcome(suite):
    test = """
import pytest

@pytest.mark.unmatched_upstream
@pytest.mark.tool("resolve_release")
def test_mapped(tools):
    tools.call("resolve_release")

@pytest.mark.tool("get_concept")
def test_absent(tools):
    tools.call("get_concept", {"code": "C3262"})
"""
    result = run(suite, test, "--report=report.json")

    result.assert_outcomes(passed=1, skipped=1)
    report = json.loads((suite.path / "report.json").read_text(encoding="utf-8"))
    tools = report["tools"]
    assert (tools["resolve_release"]["outcome"], tools["resolve_release"]["implemented_as"]) == (
        "PASS",
        "ncit_release_info",
    )
    assert tools["get_concept"]["outcome"] == "NOT IMPLEMENTED"
    assert tools["list_contexts"]["outcome"] == "NO TESTS"
    assert {nodeid: test["outcome"] for nodeid, test in report["tests"].items()} == {
        "tests/test_probe.py::test_mapped": "passed",
        "tests/test_probe.py::test_absent": "not_implemented",
    }
