"""The per-tool report: outcomes from the final test outcomes, over both run modes, rendered."""

import json
from collections import Counter
from types import SimpleNamespace

import pytest

from nci_si_acceptance.report import UNMATCHED, Collector, combine, main, render, tool_outcome
from nci_si_acceptance.spec import REQUIRED_TOOLS


@pytest.mark.parametrize(
    ("counts", "gates_failed", "implemented", "outcome"),
    [
        ({"passed": 3}, False, True, "PASS"),
        ({"passed": 2, "failed": 1}, False, True, "FAIL"),
        ({"passed": 3}, True, True, "FAIL"),
        ({"skipped": 2}, True, True, "NOT RUN"),
        ({"passed": 2, "no_fixture": 1}, False, True, "NO FIXTURE"),
        ({"no_fixture": 1, "failed": 1}, False, True, "FAIL"),
        ({"passed": 2, "skipped": 1}, False, True, "INCOMPLETE"),
        ({"passed": 2, "not_implemented": 1}, False, True, "INCOMPLETE"),
        ({"passed": 2, "not_live": 1}, False, True, "PASS"),
        ({"not_live": 2}, False, True, "NOT RUN"),
        ({"not_implemented": 2}, True, False, "NOT IMPLEMENTED"),
        ({"failed": 2}, False, False, "FAIL"),
        ({"no_fixture": 2}, False, False, "NO FIXTURE"),
        ({"not_live": 1}, False, None, "NOT RUN"),
        ({}, False, False, "NO TESTS"),
    ],
)
def test_a_tool_outcome_follows_from_its_test_outcomes_and_the_gates(
    counts, gates_failed, implemented, outcome
):
    assert tool_outcome(Counter(counts), gates_failed, implemented) == outcome


def phase(when, outcome, nodeid="t.py::test_a", reason="", unmatched=None):
    longrepr = ("t.py", 1, f"Skipped: {reason}") if outcome == "skipped" else None
    properties = [(UNMATCHED, unmatched)] if unmatched else []
    return pytest.TestReport(
        nodeid, ("t.py", 1, "test_a"), {}, outcome, longrepr, when, user_properties=properties
    )


def collected(*phases, absent=()):
    """The report of a run whose server has every required tool except those `absent`."""

    collector = Collector()
    collector.note_tools(
        SimpleNamespace(implemented_as=lambda name: None if name in absent else name)
    )
    for report, tool, gate in phases:
        collector.record(report, tool, gate)
    return collector.report("fixture")


def test_each_test_counts_once_with_its_worst_phase():
    report = collected(
        (phase("setup", "passed"), "get_concept", False),
        (phase("call", "passed"), "get_concept", False),
        (phase("teardown", "failed"), "get_concept", False),
        (phase("call", "passed", "t.py::test_b"), "get_concept", False),
    )

    row = report["tools"]["get_concept"]
    assert (row["outcome"], row["counts"]) == ("FAIL", {"failed": 1, "passed": 1})
    assert report["tests"]["t.py::test_a"]["outcome"] == "failed"


def test_a_failure_from_a_missing_fixture_is_its_own_outcome_and_names_the_request():
    missing = ["GET evs /api/v1/version {}"]
    report = collected(
        (phase("call", "failed", "t.py::test_a"), "get_concepts", False),
        (phase("teardown", "failed", "t.py::test_a", unmatched=missing), "get_concepts", False),
    )

    assert report["tools"]["get_concepts"]["outcome"] == "NO FIXTURE"
    assert report["tests"]["t.py::test_a"] == {
        "tool": "get_concepts",
        "gate": False,
        "outcome": "no_fixture",
        "unmatched": missing,
    }


def test_skips_are_not_implemented_or_not_run_and_a_failed_gate_fails_every_passing_tool():
    report = collected(
        (
            phase("call", "skipped", "t.py::b", "NOT IMPLEMENTED: get_concepts"),
            "get_concepts",
            False,
        ),
        (phase("setup", "skipped", "t.py::c", "needs a prepared index"), "list_contexts", False),
        (phase("call", "passed", "t.py::d"), "get_form", False),
        (phase("call", "failed", "t.py::gate"), None, True),
        absent=("get_concepts",),
    )

    tools = report["tools"]
    assert [tools[name]["outcome"] for name in ("get_concepts", "list_contexts", "get_form")] == [
        "NOT IMPLEMENTED",
        "NOT RUN",
        "FAIL",
    ]
    assert (report["failed_gates"], tools["get_form"]["gates_only"]) == (["t.py::gate"], True)
    assert set(tools) == set(REQUIRED_TOOLS)


def test_a_live_run_counts_a_fixture_only_test_without_making_its_tool_incomplete():
    report = collected(
        (phase("setup", "skipped", "t.py::a", "fixture mode only"), "get_form", False),
        (phase("call", "passed", "t.py::b"), "get_form", False),
        (phase("setup", "skipped", "t.py::c", "fixture mode only"), "get_concept", False),
    )

    assert report["tests"]["t.py::a"]["outcome"] == "not_live"
    assert report["tools"]["get_form"]["outcome"] == "PASS"
    assert report["tools"]["get_concept"]["outcome"] == "NOT RUN"


def test_a_tool_failing_its_own_test_while_a_gate_fails_is_not_failing_by_the_gates_only():
    report = collected(
        (phase("call", "failed", "t.py::a"), "get_form", False),
        (phase("call", "passed", "t.py::b"), "get_concept", False),
        (phase("call", "failed", "t.py::gate"), None, True),
    )

    tools = report["tools"]
    assert (tools["get_form"]["outcome"], tools["get_form"]["gates_only"]) == ("FAIL", False)
    assert (tools["get_concept"]["outcome"], tools["get_concept"]["gates_only"]) == ("FAIL", True)


def run(outcomes, tests=None):
    """A run's report with the given tool outcomes and test entries."""

    tools = {
        name: {
            "group": group,
            "outcome": "NO TESTS",
            "gates_only": False,
            "implemented_as": None,
            "counts": {},
        }
        for name, group in REQUIRED_TOOLS.items()
    }
    for name, outcome in outcomes.items():
        tools[name]["outcome"] = outcome
    return {"mode": "fixture", "failed_gates": [], "tools": tools, "tests": tests or {}}


def live_test(tool, outcome):
    return {"tool": tool, "gate": False, "outcome": outcome, "unmatched": []}


def test_a_live_failure_is_excused_only_test_by_test():
    fixture = run({"get_form": "PASS", "search_data_elements": "PASS", "get_concept": "PASS"})
    live = run(
        {},
        {
            "t.py::form_known": live_test("get_form", "failed"),
            "t.py::form_new": live_test("get_form", "failed"),
            "t.py::search_known": live_test("search_data_elements", "failed"),
            "t.py::concept": live_test("get_concept", "passed"),
        },
    )
    limitations = {"t.py::form_known": "C-4", "t.py::search_known": "C-2"}

    combined = combine(fixture, live, limitations)

    assert combined["search_data_elements"] == ("PASS (fixture only)", ["C-2"])
    assert combined["get_form"] == ("FAIL", ["C-4"])
    assert combined["get_concept"] == ("PASS", [])


def test_a_live_gate_failure_fails_every_tool_unless_it_is_a_documented_limitation():
    fixture = run({"get_form": "PASS", "get_concept": "PASS"})
    live = run({}, {"t.py::gate": {"tool": None, "gate": True, "outcome": "failed"}})
    live["failed_gates"] = ["t.py::gate"]

    assert combine(fixture, live, {})["get_form"] == ("FAIL", [])
    assert combine(fixture, live, {"t.py::gate": "P-1"})["get_concept"] == (
        "PASS (fixture only)",
        ["P-1"],
    )


def test_the_rendered_report_states_its_modes_counts_and_what_proves_nothing_yet():
    fixture = run(
        {
            "resolve_release": "FAIL",
            "get_concept": "NOT IMPLEMENTED",
            "get_concepts": "NO FIXTURE",
            "get_form": "INCOMPLETE",
        }
    )
    fixture["tools"]["resolve_release"] |= {
        "implemented_as": "ncit_release_info",
        "gates_only": True,
        "counts": {"passed": 4},
    }
    fixture["tools"]["get_form"]["counts"] = {"passed": 3, "not_implemented": 4, "skipped": 1}
    fixture["tests"] = {
        "t.py::x": live_test("get_concepts", "no_fixture") | {"unmatched": ["GET evs /x {}"]}
    }
    combined = {name: (row["outcome"], []) for name, row in fixture["tools"].items()}

    text = render(fixture, combined, "fixture only")

    assert text.startswith("Run modes: fixture only.")
    assert (
        "| `resolve_release` | A | FAIL (gates only) | 4 / 0 / 0 / 0 | ncit_release_info |  |"
        in text
    )
    assert "| `get_form` | B | INCOMPLETE | 3 / 0 / 0 / 5 | — |  |" in text
    assert "Tests run and not passing: resolve_release, get_concepts, get_form." in text
    assert "Requests without a fixture: GET evs /x {}." in text
    never_run = next(line for line in text.splitlines() if line.startswith("Tests never run"))
    assert "get_concept" in never_run
    assert "list_contexts" in never_run


def test_the_command_combines_the_runs_with_per_test_limitations(tmp_path, capsys):
    fixture = run({"get_form": "PASS"})
    live = run({}, {"t.py::form": live_test("get_form", "failed")}) | {"mode": "live"}
    (tmp_path / "fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
    (tmp_path / "live.json").write_text(json.dumps(live), encoding="utf-8")
    (tmp_path / "limitations.yaml").write_text('"t.py::form": C-4\n', encoding="utf-8")

    main(
        [
            str(tmp_path / "fixture.json"),
            "--live",
            str(tmp_path / "live.json"),
            "--limitations",
            str(tmp_path / "limitations.yaml"),
        ]
    )

    output = capsys.readouterr().out
    assert output.startswith("Run modes: fixture and live.")
    assert "| `get_form` | B | PASS (fixture only) | 0 / 0 / 0 / 0 | — | C-4 |" in output


def test_the_command_refuses_a_report_of_the_wrong_run_mode(tmp_path):
    (tmp_path / "fixture.json").write_text(json.dumps(run({})), encoding="utf-8")

    with pytest.raises(SystemExit, match="is the report of a fixture run, not of a live run"):
        main([str(tmp_path / "fixture.json"), "--live", str(tmp_path / "fixture.json")])


def test_an_empty_limitations_file_excuses_nothing(tmp_path, capsys):
    live = run({}, {"t.py::form": live_test("get_form", "failed")}) | {"mode": "live"}
    (tmp_path / "fixture.json").write_text(json.dumps(run({"get_form": "PASS"})), encoding="utf-8")
    (tmp_path / "live.json").write_text(json.dumps(live), encoding="utf-8")
    (tmp_path / "limitations.yaml").write_text("", encoding="utf-8")

    main(
        [
            str(tmp_path / "fixture.json"),
            "--live",
            str(tmp_path / "live.json"),
            "--limitations",
            str(tmp_path / "limitations.yaml"),
        ]
    )

    assert "| `get_form` | B | FAIL | 0 / 0 / 0 / 0 | — |  |" in capsys.readouterr().out


def test_the_command_says_a_fixture_run_alone_is_not_the_final_outcome(tmp_path, capsys):
    (tmp_path / "fixture.json").write_text(json.dumps(run({"get_form": "PASS"})), encoding="utf-8")

    main([str(tmp_path / "fixture.json")])

    output = capsys.readouterr().out
    assert "the live run is not included" in output
    assert "| `get_form` | B | PASS | 0 / 0 / 0 / 0 | — |  |" in output
