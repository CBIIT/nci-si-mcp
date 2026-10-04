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
