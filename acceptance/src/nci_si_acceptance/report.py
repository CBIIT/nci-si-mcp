"""The per-tool report (MCP Behavioral Acceptance Suite §6).

A run of the suite writes one report per run mode (`pytest --report=PATH`), with the
final outcome of every test. A test counts for the tool its `tool` marker names; a
test marked `gate` gates every tool (§3). One run gives each required tool one outcome:

    PASS             every test of the tool passed, and every gate
    FAIL             a test of the tool failed, or a gate did (shown as "gates only")
    NO FIXTURE       the tool's tests failed only because a request found no fixture:
                     a question for the fixture set, not a defect of the server
    NOT IMPLEMENTED  the server has the tool neither by name nor through the tool map
    NOT RUN          every test of the tool was skipped for another reason (live mode)
    NO TESTS         the suite has no test for the tool: a defect of the suite

Combining the fixture run with the live run gives the outcome of §6. A tool that
passes against fixtures is PASS (fixture only) when each of its live failures is a
test with a documented upstream limitation, and FAIL otherwise. Limitations are
documented per test, in YAML: `<test id>: <upstream requirement>`.

    python -m nci_si_acceptance.report fixture.json [--live live.json] [--limitations FILE]

PYTEST_DONT_REWRITE: the suite's conftest imports this plugin before pytest could
rewrite its assertions, and it has none.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from nci_si_acceptance.inventory import REQUIRED_TOOLS
from nci_si_acceptance.tools import NOT_IMPLEMENTED

if TYPE_CHECKING:
    from collections.abc import Iterable

    from nci_si_acceptance.tools import Tools

PASS, FAIL, NO_FIXTURE = "PASS", "FAIL", "NO FIXTURE"
NOT_RUN, NO_TESTS = "NOT RUN", "NO TESTS"
FIXTURE_ONLY = "PASS (fixture only)"
# The property the missing-fixture guard attaches to a test: the requests concerned.
UNMATCHED = "unmatched_upstream"
# A later phase of a test (setup, call, teardown) overrides an earlier outcome only
# when it ranks higher.
RANK = {"passed": 0, "skipped": 1, "not_implemented": 1, "failed": 2, "no_fixture": 3}
FAILED = ("failed", "no_fixture")


def tool_outcome(counts: Counter[str], gates_failed: bool) -> str:
    """The outcome of one tool in one run, from the final outcomes of its tests."""

    if not counts:
        return NO_TESTS
    if counts["failed"] or (gates_failed and counts["passed"]):
        return FAIL
    ranked = (("no_fixture", NO_FIXTURE), ("passed", PASS), ("not_implemented", NOT_IMPLEMENTED))
    return next((outcome for kind, outcome in ranked if counts[kind]), NOT_RUN)


def _phase_outcome(report: pytest.TestReport) -> str | None:
    if report.failed:
        return "no_fixture" if dict(report.user_properties).get(UNMATCHED) else "failed"
    if report.skipped:
        reason = str(report.longrepr[2]) if isinstance(report.longrepr, tuple) else ""
        return "not_implemented" if NOT_IMPLEMENTED in reason else "skipped"
    return "passed" if report.when == "call" else None


class Collector:
    """The final outcome of every test of a run, with its tool and the requests it lacked."""

    def __init__(self) -> None:
        self.tests: dict[str, dict[str, Any]] = {}
        self.implemented_as: dict[str, str | None] = {}

    def record(self, report: pytest.TestReport, tool: str | None, gate: bool) -> None:
        outcome = _phase_outcome(report)
        if outcome is None:
            return
        test = self.tests.setdefault(
            report.nodeid, {"tool": tool, "gate": gate, "outcome": outcome}
        )
        if RANK[outcome] >= RANK[test["outcome"]]:
            test["outcome"] = outcome
            test["unmatched"] = dict(report.user_properties).get(UNMATCHED, [])

    def note_tools(self, tools: Tools) -> None:
        self.implemented_as = {name: tools.implemented_as(name) for name in REQUIRED_TOOLS}

    def _row(self, name: str, group: str, gates_failed: bool) -> dict[str, Any]:
        counts = Counter(test["outcome"] for test in self.tests.values() if test["tool"] == name)
        return {
            "group": group,
            "outcome": tool_outcome(counts, gates_failed),
            "gates_only": gates_failed and not (counts["failed"] or counts["no_fixture"]),
            "implemented_as": self.implemented_as.get(name),
            "counts": dict(counts),
        }

    def report(self, mode: str) -> dict[str, Any]:
        gates = [
            nodeid
            for nodeid, test in self.tests.items()
            if test["gate"] and test["outcome"] in FAILED
        ]
        rows = {name: self._row(name, group, bool(gates)) for name, group in REQUIRED_TOOLS.items()}
        return {"mode": mode, "failed_gates": gates, "tools": rows, "tests": self.tests}


def combine(
    fixture: dict[str, Any], live: dict[str, Any], limitations: dict[str, str]
) -> dict[str, tuple[str, list[str]]]:
    """Each tool's outcome over both run modes (§6), with the limitations that excuse it."""

    combined = {}
    for name, row in fixture["tools"].items():
        failing = [
            nodeid
            for nodeid, test in live["tests"].items()
            if test["tool"] == name and test["outcome"] in FAILED
        ]
        combined[name] = _over_both(row["outcome"], failing, limitations)
    return combined


def _over_both(
    outcome: str, failing: list[str], limitations: dict[str, str]
) -> tuple[str, list[str]]:
    """A fixture outcome given the tool's failing live tests: excused only one by one."""

    if outcome != PASS or not failing:
        return outcome, []
    excused = sorted({limitations[nodeid] for nodeid in failing if nodeid in limitations})
    return (FIXTURE_ONLY if all(nodeid in limitations for nodeid in failing) else FAIL), excused


def _row(name: str, row: dict[str, Any], outcome: str, excused: list[str]) -> str:
    if outcome == FAIL and row["gates_only"]:
        outcome = "FAIL (gates only)"
    counts = row["counts"]
    tests = f"{counts.get('passed', 0)} / {counts.get('failed', 0)} / {counts.get('no_fixture', 0)}"
    stand_in = row["implemented_as"] or "—"
    return (
        f"| `{name}` | {row['group']} | {outcome} | {tests} | {stand_in} | {', '.join(excused)} |"
    )


def _named(combined: dict[str, tuple[str, list[str]]], *kinds: str) -> str:
    return ", ".join(name for name, (outcome, _) in combined.items() if outcome in kinds) or "none"


def render(report: dict[str, Any], combined: dict[str, tuple[str, list[str]]], modes: str) -> str:
    """The report as a Markdown table, with the tools whose tests prove nothing yet."""

    lines = [
        f"Run modes: {modes}.",
        "",
        "| Tool | Group | Outcome | Tests passed / failed / no fixture | Implemented as "
        "| Upstream limitation |",
        "|---|---|---|---|---|---|",
    ]
    lines += [_row(name, row, *combined[name]) for name, row in report["tools"].items()]
    missing = sorted(
        {request for test in report["tests"].values() for request in test.get("unmatched", [])}
    )
    lines += [
        "",
        f"Gates failed: {', '.join(report['failed_gates']) or 'none'}.",
        "Tests never run against an implementation: "
        f"{_named(combined, NOT_IMPLEMENTED, NO_TESTS)}.",
        f"Tests run and not passing: {_named(combined, FAIL)}.",
        f"Requests without a fixture: {'; '.join(missing) or 'none'}.",
    ]
    return "\n".join(lines) + "\n"


# ---- pytest plugin

COLLECTOR = pytest.StashKey[Collector]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--report", metavar="PATH", help="write the per-tool report of the run as JSON"
    )


def pytest_configure(config: pytest.Config) -> None:
    config.stash[COLLECTOR] = Collector()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    report = yield
    marker = item.get_closest_marker("tool")
    tool = marker.args[0] if marker else None
    item.config.stash[COLLECTOR].record(report, tool, item.get_closest_marker("gate") is not None)
    return report


def write_report(config: pytest.Config, mode: str) -> None:
    """Write the run's report where `--report` says, if it says."""

    path = config.getoption("report")
    if path:
        Path(path).write_text(json.dumps(config.stash[COLLECTOR].report(mode), indent=2) + "\n")


def main(arguments: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the per-tool acceptance report.")
    parser.add_argument("fixture", type=Path, help="the report of the fixture-mode run")
    parser.add_argument("--live", type=Path, help="the report of the live run")
    parser.add_argument("--limitations", type=Path, help="YAML: test id -> upstream requirement")
    options = parser.parse_args(arguments)
    fixture = json.loads(options.fixture.read_text(encoding="utf-8"))
    if options.live:
        limitations = {}
        if options.limitations:
            limitations = yaml.safe_load(options.limitations.read_text(encoding="utf-8")) or {}
        live = json.loads(options.live.read_text(encoding="utf-8"))
        combined, modes = combine(fixture, live, limitations), "fixture and live"
    else:
        combined = {name: (row["outcome"], []) for name, row in fixture["tools"].items()}
        modes = "fixture only; the live run is not included, so no outcome here is §6's final one"
    sys.stdout.write(render(fixture, combined, modes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
