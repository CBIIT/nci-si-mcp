"""The expected outcomes: a report is compared with them test by test, and they are rewritten
from a report on request."""

import json

import pytest

from nci_si_acceptance.expected import (
    ABSENT_FROM_EXPECTED,
    ABSENT_FROM_REPORT,
    EXPECTED,
    differences,
    main,
)
from nci_si_acceptance.report import RANK


def report(tests, mode):
    return {
        "mode": mode,
        "transport": "stdio",
        "run": {
            "exit_status": 1,
            "selected": len(tests),
            "finished": len(tests),
            "worker_crashes": 0,
        },
        "tests": {nodeid: {"tool": "get_form", "outcome": tests[nodeid]} for nodeid in tests},
    }


@pytest.fixture
def files(tmp_path):
    """Write the expected outcomes and a report; return the arguments of `check`."""

    def write(expected, tests, mode="fixture"):
        (tmp_path / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
        (tmp_path / "report.json").write_text(json.dumps(report(tests, mode)), encoding="utf-8")
        return [
            "check",
            str(tmp_path / "report.json"),
            "--expected",
            str(tmp_path / "expected.json"),
        ]

    return write


def test_equal_outcomes_are_no_difference(files, capsys):
    arguments = files(
        {"t.py::a": "passed", "t.py::b": "failed"}, {"t.py::b": "failed", "t.py::a": "passed"}
    )

    assert main(arguments) == 0
    assert capsys.readouterr().out == "All 2 tests have the outcome expected.\n"


def test_a_changed_outcome_is_a_difference_in_a_table_with_the_hint(files, capsys):
    arguments = files(
        {"t.py::a": "passed", "t.py::b": "failed"}, {"t.py::a": "passed", "t.py::b": "passed"}
    )

    assert main(arguments) == 1
    output = capsys.readouterr().out
    assert "1 of 2 tests differ from the expected outcomes." in output
    assert "| `t.py::b` | failed | passed |" in output
    assert "t.py::a" not in output
    assert "pdm run acceptance-expected update" in output


def test_a_test_the_report_lacks_and_one_the_expected_outcomes_lack_are_differences(files, capsys):
    arguments = files({"t.py::gone": "passed"}, {"t.py::new": "passed"})

    assert main(arguments) == 1
    output = capsys.readouterr().out
    assert f"| `t.py::gone` | passed | {ABSENT_FROM_REPORT} |" in output
    assert f"| `t.py::new` | {ABSENT_FROM_EXPECTED} | passed |" in output


def test_a_pipe_in_a_test_id_does_not_break_the_table(files, capsys):
    main(files({"t.py::a[x|y]": "passed"}, {"t.py::a[x|y]": "failed"}))

    assert "| `t.py::a[x\\|y]` | passed | failed |" in capsys.readouterr().out


def test_a_report_of_a_live_run_is_refused(files):
    with pytest.raises(SystemExit, match="is the report of a live run, not a fixture run"):
        main(files({}, {}, mode="live"))


def test_differences_are_listed_in_test_order():
    found = differences({"b": "passed", "a": "passed"}, {"b": "failed", "a": "failed"})

    assert [test for test, _, _ in found] == ["a", "b"]


def test_update_rewrites_the_outcomes_from_a_report_and_nothing_else(files, tmp_path, capsys):
    arguments = files({"t.py::old": "passed"}, {"t.py::b": "failed", "t.py::a": "not_implemented"})

    assert main(["update", *arguments[1:]]) == 0

    written = json.loads((tmp_path / "expected.json").read_text(encoding="utf-8"))
    assert written == {"t.py::a": "not_implemented", "t.py::b": "failed"}
    assert list(written) == ["t.py::a", "t.py::b"]
    assert "Wrote the outcomes of 2 tests" in capsys.readouterr().out
    assert main(arguments) == 0


def test_update_writes_no_message_or_request_the_report_holds(files, tmp_path):
    arguments = files({}, {"t.py::a": "failed"})
    detailed = report({"t.py::a": "no_fixture"}, "fixture")
    detailed["tests"]["t.py::a"] |= {"message": "assert 1 == 2", "unmatched": ["GET evs /x {}"]}
    (tmp_path / "report.json").write_text(json.dumps(detailed), encoding="utf-8")

    main(["update", *arguments[1:]])

    written = (tmp_path / "expected.json").read_text(encoding="utf-8")
    assert json.loads(written) == {"t.py::a": "no_fixture"}
    assert "assert" not in written
    assert "GET" not in written


@pytest.mark.parametrize(
    ("expected", "actual"),
    [("not_implemented", "not_live"), ("not_live", "not_implemented")],
)
def test_outcomes_that_share_a_prefix_are_a_difference(files, capsys, expected, actual):
    assert main(files({"t.py::a": expected}, {"t.py::a": actual})) == 1
    assert f"| `t.py::a` | {expected} | {actual} |" in capsys.readouterr().out


def test_the_committed_outcomes_are_in_the_reports_vocabulary_and_in_test_order():
    committed = json.loads(EXPECTED.read_text(encoding="utf-8"))

    assert set(committed.values()) <= set(RANK)
    assert list(committed) == sorted(committed)
