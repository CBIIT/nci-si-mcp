"""Every test of the suite is attributed, to a tool or as a gate, or the run is refused; the
permission tests (X-25 to X-28) are gates, so a server that fails them fails every tool."""

import json
import shutil

import pytest
from conftest import STAND_IN, SUITE

pytest_plugins = ["pytester"]

UNATTRIBUTED = "def test_nobody_is_told_of_this():\n    assert True\n"


def _run(compliant, *arguments):
    report = compliant.path / "report.json"
    result = compliant.runpytest_subprocess(
        *arguments, "-p", "no:cacheprovider", "-p", STAND_IN, f"--report={report}"
    )
    return result, report


def test_a_collected_test_with_neither_tool_nor_gate_refuses_the_run_and_is_named(compliant):
    (compliant.path / "tests" / "test_unattributed.py").write_text(UNATTRIBUTED, encoding="utf-8")

    result, report = _run(compliant, "tests/test_unattributed.py")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*tests/test_unattributed.py::test_nobody_is_told_of_this*"])
    assert json.loads(report.read_text(encoding="utf-8"))["tests"] == {}


def test_the_permission_tests_are_gates_that_the_report_lists_when_they_did_not_run(compliant):
    shutil.copy(SUITE / "test_permissions.py", compliant.path / "tests" / "test_permissions.py")

    result, report = _run(compliant, "tests/test_permissions.py")

    assert result.ret == pytest.ExitCode.OK
    found = json.loads(report.read_text(encoding="utf-8"))
    tests = {nodeid.partition("::")[2].partition("[")[0] for nodeid in found["tests"]}
    unrun = {nodeid.partition("::")[2].partition("[")[0] for nodeid in found["unrun_gates"]}
    assert tests
    assert unrun == tests
