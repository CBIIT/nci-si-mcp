"""A run of the suite, end to end, from a copy of the suite's conftest and the furnished server.

These check what only a whole run shows: the missing-fixture guard, at startup too, a
scenario served with its settings to a server process of its own, the tool map, and the
per-tool report.
"""

import json
import shlex
import sys
from pathlib import Path

import pytest
from conftest import STAND_IN, stand_in

pytest_plugins = ["pytester"]

SUITE_CONFTEST = Path(__file__).parent.parent / "tests" / "conftest.py"
# These probes call discovery directly; remaining stand-ins are checked separately.
TOOLMAP = "{}\n"
DISCOVERY_REQUEST = {"surface": "evs", "method": "GET", "path": "/api/v1/metadata/terminologies"}


def fixture(directory, name, response, **document):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "kind": "crafted",
        "requirement": "self-test",
        "request": DISCOVERY_REQUEST,
    } | document
    path.write_text(json.dumps(document | {"response": response}), encoding="utf-8")


@pytest.fixture
def suite(pytester, monkeypatch):
    """A copy of the suite's conftest, with a tool map and no fixtures yet."""

    fixtures = pytester.mkdir("fixtures")
    (fixtures / "baseline_toolmap.yaml").write_text(TOOLMAP, encoding="utf-8")
    tests = pytester.mkdir("tests")
    stand_in(pytester)
    (tests / "conftest.py").write_text(SUITE_CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_MODE", "fixture")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_SERVER", f"{sys.executable} -m nci_si_mcp.cli serve")
    return pytester


def run(suite, test, *options):
    (suite.path / "tests" / "test_probe.py").write_text(test, encoding="utf-8")
    return suite.runpytest_subprocess("tests", "-p", "no:cacheprovider", "-p", STAND_IN, *options)


PROBE = """
import pytest

@pytest.mark.tool("list_terminologies")
{marker}
def test_probe(tools):
    assert tools.call("list_terminologies").tool == "list_terminologies"
"""


def test_requests_without_a_fixture_fail_the_test_and_are_listed(suite):
    result = run(suite, PROBE.format(marker=""), "--report=report.json")

    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(
        [
            "E * upstream requests without a fixture:",
            "E * GET evs /api/v1/metadata/terminologies {}",
        ]
    )
    report = json.loads((suite.path / "report.json").read_text(encoding="utf-8"))
    assert report["tools"]["list_terminologies"]["outcome"] == "NO FIXTURE"


# A server that asks EVS for its terminology listing before it serves.
EAGER_SERVER = """
import os, runpy, sys, urllib.error, urllib.request
try:
    urllib.request.urlopen(os.environ["NCI_SI_EVS_BASE_URL"] + "/api/v1/metadata/terminologies")
except urllib.error.HTTPError:
    pass
sys.argv = ["nci-si-mcp", "serve"]
runpy.run_module("nci_si_mcp.cli", run_name="__main__")
"""


def eager(suite, monkeypatch):
    (suite.path / "eager.py").write_text(EAGER_SERVER, encoding="utf-8")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_SERVER", f"{sys.executable} {suite.path / 'eager.py'}")


def test_requests_without_a_fixture_while_the_server_starts_fail_every_test_using_it(
    suite, monkeypatch
):
    eager(suite, monkeypatch)
    test = (
        PROBE.format(marker="@pytest.mark.unmatched_upstream")
        + """
@pytest.mark.tool("list_terminologies")
def test_again(tools):
    tools.call("list_terminologies")
"""
    )
    result = run(suite, test, "--report=report.json")

    result.assert_outcomes(errors=2)
    result.stdout.fnmatch_lines(
        [
            "E * upstream requests without a fixture while the server started:",
            "E * GET evs /api/v1/metadata/terminologies {}",
        ]
    )
    report = json.loads((suite.path / "report.json").read_text(encoding="utf-8"))
    assert report["tools"]["list_terminologies"]["outcome"] == "NO FIXTURE"
    assert {test["outcome"] for test in report["tests"].values()} == {"no_fixture"}
    assert all(
        test["unmatched"] == ["GET evs /api/v1/metadata/terminologies {}"]
        for test in report["tests"].values()
    )


def test_a_test_sees_none_of_the_requests_its_server_made_while_it_started(suite, monkeypatch):
    eager(suite, monkeypatch)
    fixture(
        suite.path / "fixtures",
        "scenarios/probe/up/terminologies.json",
        {"status": 200, "body": []},
    )
    test = """
import pytest

@pytest.mark.scenario("probe/up")
def test_clean(tools, upstream):
    assert upstream.log() == []
"""
    result = run(suite, test)

    result.assert_outcomes(passed=1)


def test_a_server_starting_after_another_test_is_not_charged_with_its_requests(suite):
    test = """
import urllib.error, urllib.request
import pytest

@pytest.mark.unmatched_upstream
def test_first(upstream):
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(upstream.url + "/evs/api/v1/elsewhere")

def test_second(server):
    assert server
"""
    result = run(suite, test)

    result.assert_outcomes(passed=2)


def test_a_live_run_reports_its_mode_and_a_tool_with_only_fixture_tests_as_not_run(
    suite, monkeypatch
):
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_MODE", "live")

    result = run(suite, PROBE.format(marker=""), "--report=report.json")

    result.assert_outcomes(skipped=1)
    report = json.loads((suite.path / "report.json").read_text(encoding="utf-8"))
    assert (report["mode"], report["tools"]["list_terminologies"]["outcome"]) == ("live", "NOT RUN")


def test_a_test_marked_unmatched_upstream_may_leave_them(suite):
    result = run(suite, PROBE.format(marker="@pytest.mark.unmatched_upstream"))

    result.assert_outcomes(passed=1)


def test_a_scenario_is_served_to_a_server_of_its_own_and_ends_with_the_test(suite):
    fixtures = suite.path / "fixtures"
    fixture(fixtures, "recorded/terminologies.json", {"status": 200, "body": []})
    fixture(
        fixtures,
        "scenarios/probe/other/terminologies.json",
        {"status": 200, "body": []},
    )
    test = """
import pytest

def recordings(upstream):
    return [
        entry["fixture"] for entry in upstream.log()
        if entry["path"] == "/api/v1/metadata/terminologies"
    ]

@pytest.mark.unmatched_upstream
@pytest.mark.scenario("probe/other")
def test_during(tools, server, upstream):
    assert tools is not server
    tools.call("list_terminologies")
    assert recordings(upstream) == ["scenarios/probe/other/terminologies.json"]

@pytest.mark.unmatched_upstream
def test_after(tools, upstream):
    tools.call("list_terminologies")
    assert recordings(upstream) == ["recorded/terminologies.json"]
"""
    result = run(suite, test)

    result.assert_outcomes(passed=2)


def test_a_scenario_starts_its_server_with_its_settings(suite):
    scenario = suite.path / "fixtures" / "scenarios" / "probe" / "single-attempt"
    fixture(scenario, "terminologies.json", {"status": 503, "body": {"message": "down"}})
    (scenario / "settings.json").write_text('{"NCI_SI_EVS_MAX_ATTEMPTS": "1"}', encoding="utf-8")
    test = """
import pytest

def attempts(upstream):
    return [entry for entry in upstream.log() if entry["path"] == "/api/v1/metadata/terminologies"]

@pytest.mark.unmatched_upstream
@pytest.mark.scenario("probe/single-attempt")
def test_once(tools, upstream):
    tools.call("list_terminologies")
    assert len(attempts(upstream)) == 1
"""
    result = run(suite, test)

    result.assert_outcomes(passed=1)


def test_a_failed_gate_fails_every_tool_whose_own_tests_pass(suite):
    fixture(suite.path / "fixtures", "recorded/terminologies.json", {"status": 200, "body": []})
    test = """
import pytest

@pytest.mark.unmatched_upstream
@pytest.mark.tool("list_terminologies")
def test_tool(tools):
    tools.call("list_terminologies")

@pytest.mark.gate
def test_gate():
    assert False, "the gate does not hold"
"""
    result = run(suite, test, "--report=report.json")

    result.assert_outcomes(passed=1, failed=1)
    report = json.loads((suite.path / "report.json").read_text(encoding="utf-8"))
    assert report["failed_gates"] == ["tests/test_probe.py::test_gate"]
    assert (
        report["tools"]["list_terminologies"]["outcome"],
        report["tools"]["list_terminologies"]["gates_only"],
    ) == ("FAIL", True)


def test_the_report_gives_each_required_tool_its_outcome(suite):
    test = """
import pytest

@pytest.mark.unmatched_upstream
@pytest.mark.tool("list_terminologies")
def test_direct(tools):
    tools.call("list_terminologies")

@pytest.mark.tool("get_form")
def test_absent(tools):
    tools.call("get_form", {"publicId": "123"})
"""
    result = run(suite, test, "--report=report.json")

    result.assert_outcomes(passed=1, skipped=1)
    report = json.loads((suite.path / "report.json").read_text(encoding="utf-8"))
    tools = report["tools"]
    assert (
        tools["list_terminologies"]["outcome"],
        tools["list_terminologies"]["implemented_as"],
    ) == (
        "PASS",
        "list_terminologies",
    )
    assert tools["get_form"]["outcome"] == "NOT IMPLEMENTED"
    assert tools["list_contexts"]["outcome"] == "NO TESTS"
    assert {nodeid: test["outcome"] for nodeid, test in report["tests"].items()} == {
        "tests/test_probe.py::test_direct": "passed",
        "tests/test_probe.py::test_absent": "not_implemented",
    }


def test_the_report_names_the_stand_in_of_each_mapped_tool_with_the_real_map(suite):
    real = Path(__file__).parent.parent / "fixtures" / "baseline_toolmap.yaml"
    (suite.path / "fixtures" / "baseline_toolmap.yaml").write_text(
        real.read_text(encoding="utf-8"), encoding="utf-8"
    )
    test = """
import pytest

@pytest.mark.unmatched_upstream
@pytest.mark.tool("get_concept_hierarchy")
def test_parent(tools):
    result = tools.call("get_concept_hierarchy", {
        "terminology": "ncit", "code": "C3262", "direction": "parent", "depth": 1
    })
    assert result.tool == "ncit_traverse"

@pytest.mark.tool("get_concept_hierarchy")
def test_paths_to_root(tools):
    tools.call("get_concept_hierarchy", {
        "terminology": "ncit", "code": "C3262", "direction": "pathsToRoot"
    })
"""
    result = run(suite, test, "--report=report.json")

    result.assert_outcomes(passed=1, skipped=1)
    tools = json.loads((suite.path / "report.json").read_text(encoding="utf-8"))["tools"]
    assert (
        tools["get_concept_hierarchy"]["outcome"],
        tools["get_concept_hierarchy"]["counts"],
    ) == (
        "INCOMPLETE",
        {"passed": 1, "not_implemented": 1},
    )
    implemented = {
        name: row["implemented_as"] for name, row in tools.items() if row["implemented_as"]
    }
    assert implemented == {
        "resolve_release": "resolve_release",
        "list_terminologies": "list_terminologies",
        "get_concept": "ncit_lookup",
        "get_concept_hierarchy": "ncit_traverse",
        "get_concept_neighborhood": "ncit_traverse",
    }


# A fixture set's manifest with an index set: the concepts recorded at an include that holds
# the summary, not those recorded minimal.
INDEXED = """evs:
  release: ncit_26.09d
record:
  concepts:
    full: [C4817]
    summary,parents: [C3262]
    minimal: [C2991]
"""
PREPARED_PROBE = """
import pytest

@pytest.mark.prepared
@pytest.mark.tool("list_terminologies")
def test_shared(tools):
    codes = (tools.process.data / "codes.txt").read_text(encoding="utf-8")
    assert codes.split() == ["C4817", "C3262"]
    # What a server writes stays in its own copy (the next test starts another).
    (tools.process.data / "written.txt").write_text("shared", encoding="utf-8")

@pytest.mark.prepared
@pytest.mark.own_server
@pytest.mark.tool("list_terminologies")
def test_own(tools):
    assert (tools.process.data / "codes.txt").exists()
    assert not (tools.process.data / "written.txt").exists()

@pytest.mark.unprepared
@pytest.mark.tool("list_terminologies")
def test_unprepared(tools):
    assert not (tools.process.data / "codes.txt").exists()
"""


def prepare(suite, monkeypatch, script):
    """The prepare command: `script` run by this Python, with the data directory and the
    index set's file in its environment."""

    (suite.path / "fixtures" / "manifest.yaml").write_text(INDEXED, encoding="utf-8")
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_PREPARE", command)


def test_the_prepare_command_runs_once_and_every_server_starts_from_its_data(suite, monkeypatch):
    runs = suite.path / "runs.txt"
    prepare(
        suite,
        monkeypatch,
        "import os, pathlib\n"
        f"with open({str(runs)!r}, 'a') as runs: runs.write('run\\n')\n"
        "codes = pathlib.Path(os.environ['NCI_SI_ACCEPTANCE_INDEX_CODES']).read_text()\n"
        "pathlib.Path(os.environ['NCI_SI_DATA_DIR'], 'codes.txt').write_text(codes)\n",
    )

    result = run(suite, PREPARED_PROBE)

    result.assert_outcomes(passed=3)
    assert runs.read_text(encoding="utf-8").splitlines() == ["run"]


def test_without_a_prepare_command_a_test_that_needs_it_is_not_run(suite):
    result = run(suite, PREPARED_PROBE, "-rs")

    result.assert_outcomes(passed=1, skipped=2)
    result.stdout.fnmatch_lines(["*NOT RUN: no prepare command*"])


@pytest.mark.parametrize(
    ("script", "said"),
    [
        (
            "import sys\nsys.stderr.write('no index built\\n')\nraise SystemExit(3)",
            "*no index built*",
        ),
        (
            "import os, urllib.request\n"
            "url = os.environ['NCI_SI_EVS_BASE_URL'] + '/api/v1/metadata/terminologies'\n"
            "try: urllib.request.urlopen(url)\n"
            "except OSError: pass\n",
            "*upstream requests without a fixture while preparing*",
        ),
    ],
    ids=["failing", "unanswered"],
)
def test_a_prepare_command_that_fails_or_asks_without_a_fixture_ends_the_run(
    suite, monkeypatch, script, said
):
    prepare(suite, monkeypatch, script)

    result = run(suite, PREPARED_PROBE)

    assert result.ret != 0
    result.assert_outcomes()
    result.stdout.fnmatch_lines([said])
