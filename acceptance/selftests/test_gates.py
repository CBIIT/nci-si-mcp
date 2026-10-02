"""The protocol gates pass against a server that meets them, and each fails on its own defect.

The suite's own conftest and test_protocol.py run against `gate_server.py`, a server of the
evs profile, with fixtures for the one request it makes.
"""

import json
import sys
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

SUITE = Path(__file__).parent.parent / "tests"
GATE_SERVER = Path(__file__).parent / "gate_server.py"
VERSION = {"surface": "evs", "method": "GET", "path": "/api/v1/version"}
# Each defect of the gate server, and the gate test that must fail on it.
DEFECTS = [
    ("missing-tool", "test_tools_list_names_the_tools_of_the_profile_and_no_other"),
    (
        "no-error-shape",
        "test_every_output_schema_admits_the_error_record_and_refuses_a_malformed_one",
    ),
    (
        "declares-no-shape",
        "test_every_output_schema_admits_the_error_record_and_refuses_a_malformed_one",
    ),
    ("placeholder", "test_no_description_holds_placeholder_or_debug_text"),
    ("misnamed", "test_tool_names_are_verb_led_lowercase_and_underscore_separated"),
    ("no-ttl", "test_tools_list_may_be_cached_and_shared"),
    (
        "listing-changes",
        "test_tools_list_is_the_same_after_a_call_that_pins_a_terminology_and_release",
    ),
    ("hides-tools", "test_tools_list_is_the_same_while_the_platform_is_unavailable"),
    ("no-correlation", "test_a_correlation_identifier_goes_upstream_and_comes_back"),
    ("destructive", "test_every_tool_is_annotated_read_only_idempotent_and_open_world"),
    ("parameter-renamed", "test_each_tool_takes_the_parameters_the_specification_names"),
]


def _fixture(path: Path, status: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"kind": "crafted", "requirement": "self-test", "request": VERSION}
    path.write_text(json.dumps(document | {"response": {"status": status, "body": {}}}))


@pytest.fixture
def gates(pytester, monkeypatch):
    """The gates of the suite, against the gate server, its platform up unless a scenario says."""

    fixtures = pytester.mkdir("fixtures")
    (fixtures / "manifest.yaml").write_text("evs:\n  release: ncit_26.09d\n", encoding="utf-8")
    _fixture(fixtures / "crafted" / "version.json", 200)
    _fixture(fixtures / "scenarios" / "upstream" / "unavailable" / "version.json", 503)
    tests = pytester.mkdir("tests")
    for name in ("conftest.py", "test_protocol.py"):
        (tests / name).write_text((SUITE / name).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_MODE", "fixture")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_SERVER", f"{sys.executable} {GATE_SERVER}")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_PROFILE", "evs")
    monkeypatch.delenv("GATE_SERVER_DEFECT", raising=False)
    return pytester


def outcomes(gates):
    """The outcome of each gate test, by its name."""

    report = gates.path / "report.json"
    gates.runpytest_subprocess("tests", "-p", "no:cacheprovider", f"--report={report}")
    tests = json.loads(report.read_text(encoding="utf-8"))["tests"]
    return {nodeid.partition("::")[2]: test["outcome"] for nodeid, test in tests.items()}


def test_every_gate_passes_against_a_server_that_meets_them(gates):
    passed = outcomes(gates)

    assert set(passed.values()) == {"passed"}
    assert set(passed) == {gate for _, gate in DEFECTS}


@pytest.mark.parametrize(("defect", "gate"), DEFECTS)
def test_each_gate_fails_on_its_own_defect(gates, monkeypatch, defect, gate):
    monkeypatch.setenv("GATE_SERVER_DEFECT", defect)

    assert outcomes(gates)[gate] == "failed"
