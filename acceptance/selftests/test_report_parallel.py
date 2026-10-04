"""A run on several xdist workers writes the report a serial run writes: the controller process
that writes it sees only what the workers forward."""

import json

from conftest import STAND_IN

pytest_plugins = ["pytester"]


def written(compliant, *options):
    report = compliant.path / "report.json"
    report.unlink(missing_ok=True)
    compliant.runpytest_subprocess(
        "tests", "-p", "no:cacheprovider", "-p", STAND_IN, f"--report={report}", *options
    )
    return json.loads(report.read_text(encoding="utf-8"))


def test_a_report_of_a_run_on_two_workers_equals_the_report_of_a_serial_run(compliant):
    selection = ("-k", "test_protocol or test_each_tool_has_a_call")

    serial = written(compliant, *selection)
    parallel = written(compliant, *selection, "-n", "2")

    assert serial["tests"]
    assert parallel["tests"] == serial["tests"]
    assert parallel["tools"] == serial["tools"]
    assert parallel == serial
    assert list(parallel["tests"]) == sorted(parallel["tests"])


def test_a_report_of_a_real_run_attributes_each_test_to_its_tool_and_gate(compliant):
    report = written(compliant, "-k", "test_protocol or test_crosscutting and get_concepts")

    attributed = {(test["tool"], test["gate"]) for test in report["tests"].values()}
    assert attributed == {(None, True), ("get_concepts", False)}
    tool_tests = [test for test in report["tests"].values() if test["tool"] == "get_concepts"]
    assert report["tools"]["get_concepts"]["counts"] == {"passed": len(tool_tests)}
    assert report["tools"]["get_concepts"]["outcome"] == "PASS"
    assert report["tools"]["get_concept"]["counts"] == {}
