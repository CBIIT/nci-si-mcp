"""The per-tool report: outcomes from test outcomes, over both run modes, rendered."""

import json

import pytest

from nci_si_acceptance.inventory import REQUIRED_TOOLS
from nci_si_acceptance.report import Collector, combine, main, render, tool_outcome


def counts(passed=0, failed=0, not_implemented=0, skipped=0):
    return {
        "passed": passed,
        "failed": failed,
        "not_implemented": not_implemented,
        "skipped": skipped,
    }


@pytest.mark.parametrize(
    ("tests", "gates_failed", "outcome"),
    [
        (counts(passed=3), False, "PASS"),
        (counts(passed=2, failed=1), False, "FAIL"),
        (counts(passed=3), True, "FAIL"),
        (counts(not_implemented=2), True, "NOT IMPLEMENTED"),
        (counts(skipped=2), False, "NOT RUN"),
        (counts(), False, "NO TESTS"),
    ],
)
def test_a_tool_outcome_follows_from_its_test_outcomes_and_the_gates(tests, gates_failed, outcome):
    assert tool_outcome(tests, gates_failed) == outcome


def report(when, outcome, nodeid="t.py::test_a", reason=""):
    longrepr = ("t.py", 1, f"Skipped: {reason}") if outcome == "skipped" else None
    return pytest.TestReport(nodeid, ("t.py", 1, "test_a"), {}, outcome, longrepr, when)


def test_the_collector_counts_each_test_once_and_a_failure_in_any_phase_wins():
    collector = Collector()
    collector.record(report("setup", "passed"), "get_concept", gate=False)
    collector.record(report("call", "passed"), "get_concept", gate=False)
    collector.record(report("teardown", "failed"), "get_concept", gate=False)
    collector.record(
        report("call", "skipped", "t.py::test_b", "NOT IMPLEMENTED: get_concepts"),
        "get_concepts",
        gate=False,
    )
    collector.record(
        report("setup", "skipped", "t.py::test_c", "fixture mode only"), "list_contexts", gate=False
    )
    collector.record(report("call", "failed", "t.py::test_gate"), None, gate=True)

    result = collector.report("fixture")

    assert result["tests"] == {
        "t.py::test_a": "failed",
        "t.py::test_b": "not_implemented",
        "t.py::test_c": "skipped",
        "t.py::test_gate": "failed",
    }
    assert result["failed_gates"] == ["t.py::test_gate"]
    assert [
        result["tools"][name]["outcome"]
        for name in ("get_concept", "get_concepts", "list_contexts")
    ] == [
        "FAIL",
        "NOT IMPLEMENTED",
        "NOT RUN",
    ]
    assert set(result["tools"]) == set(REQUIRED_TOOLS)


def run_report(outcomes):
    tools = {
        name: {"group": REQUIRED_TOOLS[name], "outcome": "NO TESTS", "implemented_as": None}
        for name in REQUIRED_TOOLS
    }
    for name, outcome in outcomes.items():
        tools[name] |= {"outcome": outcome}
    return {"mode": "fixture", "failed_gates": [], "tools": tools, "tests": {}}


def test_a_live_failure_is_fixture_only_where_a_limitation_explains_it_and_fail_elsewhere():
    fixture = run_report(
        {"get_concept": "PASS", "search_data_elements": "PASS", "get_form": "PASS"}
    )
    live = run_report({"get_concept": "PASS", "search_data_elements": "FAIL", "get_form": "FAIL"})

    outcomes = combine(fixture, live, {"search_data_elements": "C-2"})

    assert (outcomes["get_concept"], outcomes["search_data_elements"], outcomes["get_form"]) == (
        "PASS",
        "PASS (fixture only)",
        "FAIL",
    )


def test_the_rendered_report_names_the_tools_whose_tests_prove_nothing_yet():
    fixture = run_report({"resolve_release": "FAIL", "get_concept": "NOT IMPLEMENTED"})
    fixture["tools"]["resolve_release"]["implemented_as"] = "ncit_release_info"
    outcomes = {name: row["outcome"] for name, row in fixture["tools"].items()}

    text = render(fixture, outcomes, {})

    assert "| `resolve_release` | A | FAIL | ncit_release_info |  |" in text
    assert "Tests run and not passing: resolve_release." in text
    never_run = next(line for line in text.splitlines() if line.startswith("Tests never run"))
    assert "get_concept" in never_run
    assert "list_contexts" in never_run


def test_the_command_combines_the_runs_and_prints_the_table(tmp_path, capsys):
    (tmp_path / "fixture.json").write_text(
        json.dumps(run_report({"get_form": "PASS"})), encoding="utf-8"
    )
    (tmp_path / "live.json").write_text(
        json.dumps(run_report({"get_form": "FAIL"})), encoding="utf-8"
    )
    (tmp_path / "limitations.yaml").write_text("get_form: C-4\n", encoding="utf-8")

    main(
        [
            str(tmp_path / "fixture.json"),
            "--live",
            str(tmp_path / "live.json"),
            "--limitations",
            str(tmp_path / "limitations.yaml"),
        ]
    )

    assert "| `get_form` | B | PASS (fixture only) | — | C-4 |" in capsys.readouterr().out


def test_the_command_reports_a_single_run_as_it_is(tmp_path, capsys):
    (tmp_path / "fixture.json").write_text(
        json.dumps(run_report({"get_form": "FAIL"})), encoding="utf-8"
    )

    main([str(tmp_path / "fixture.json")])

    assert "| `get_form` | B | FAIL | — |  |" in capsys.readouterr().out
