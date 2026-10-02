"""The per-tool report (MCP Behavioral Acceptance Suite §6).

A run of the suite writes one report per run mode (`pytest --report=PATH`), with the
final outcome of every test. A test counts for the tool its `tool` marker names; a
test marked `gate` gates every tool (§3). One run gives each required tool one outcome:

    PASS             every test of the tool ran and passed, and every gate
    FAIL             a test of the tool failed, or a gate did (shown as "gates only")
    NO FIXTURE       the tool's tests failed only because a request found no fixture:
                     a question for the fixture set, not a defect of the server
    INCOMPLETE       the tool's tests that ran passed, but some could not run: skipped, or
                     needing a capability the tool (or its stand-in) lacks; a hardening
                     candidate, not a pass
    NOT IMPLEMENTED  the server has the tool neither by name nor through the tool map
    NOT RUN          no test of the tool ran (in live mode: none is live-capable)
    NO TESTS         the suite has no test for the tool: a defect of the suite

In a live run, the tests that run in fixture mode only leave a tool NOT RUN, never INCOMPLETE.

Combining the fixture run with the live run gives the outcome of §6. A tool that
passes against fixtures is PASS (fixture only) when each of its live failures is a
test with a documented upstream limitation, and FAIL otherwise; a gate that fails live
fails every tool in the same way. Limitations are documented per test, in YAML:
`<test id>: <upstream requirement>`.

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
from nci_si_acceptance.suite import FIXTURE_ONLY as FIXTURE_ONLY_SKIP
from nci_si_acceptance.suite import UnmatchedUpstream
from nci_si_acceptance.tools import NOT_IMPLEMENTED

if TYPE_CHECKING:
    from collections.abc import Iterable

    from nci_si_acceptance.tools import Tools

PASS, FAIL, NO_FIXTURE = "PASS", "FAIL", "NO FIXTURE"
INCOMPLETE, NOT_RUN, NO_TESTS = "INCOMPLETE", "NOT RUN", "NO TESTS"
FIXTURE_ONLY = "PASS (fixture only)"
# The property a test failing for want of fixtures carries: the requests concerned.
UNMATCHED = "unmatched_upstream"
# A later phase of a test (setup, call, teardown) overrides an earlier outcome only
# when it ranks higher.
RANK = {
    "passed": 0,
    "skipped": 1,
    "not_implemented": 1,
    "not_live": 1,
    "failed": 2,
    "no_fixture": 3,
}
FAILED = ("failed", "no_fixture")
# The final outcomes of a test that did not run to a verdict.
UNRUN = ("skipped", "not_implemented", "not_live")


def tool_outcome(counts: Counter[str], gates_failed: bool, implemented: bool | None) -> str:
    """The outcome of one tool in one run, from the final outcomes of its tests.

    `implemented` is None when no server started, so the run cannot tell.
    """

    if not counts:
        return NO_TESTS
    if counts["failed"] or (gates_failed and counts["passed"]):
        return FAIL
    if counts["no_fixture"]:
        return NO_FIXTURE
    return _without_failures(counts, implemented)


def _without_failures(counts: Counter[str], implemented: bool | None) -> str:
    if implemented is False:
        return NOT_IMPLEMENTED
    if not counts["passed"]:
        return NOT_RUN
    return INCOMPLETE if counts["skipped"] or counts["not_implemented"] else PASS


def _skip_outcome(report: pytest.TestReport) -> str:
    reason = str(report.longrepr[2]) if isinstance(report.longrepr, tuple) else ""
    if FIXTURE_ONLY_SKIP in reason:
        return "not_live"
    return "not_implemented" if NOT_IMPLEMENTED in reason else "skipped"


def _phase_outcome(report: pytest.TestReport) -> str | None:
    if report.failed:
        return "no_fixture" if dict(report.user_properties).get(UNMATCHED) else "failed"
    if report.skipped:
        return _skip_outcome(report)
    return "passed" if report.when == "call" else None


class Collector:
    """The final outcome of every test of a run, with its tool and the requests it lacked."""

    def __init__(self) -> None:
        self.tests: dict[str, dict[str, Any]] = {}
        # Which tool stands for each required one; None until a server has started.
        self.implemented_as: dict[str, str | None] | None = None

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
        stand_in = (self.implemented_as or {}).get(name)
        implemented = None if self.implemented_as is None else stand_in is not None
        return {
            "group": group,
            "outcome": tool_outcome(counts, gates_failed, implemented),
            "gates_only": gates_failed and not (counts["failed"] or counts["no_fixture"]),
            "implemented_as": stand_in,
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
        failing = live["failed_gates"] + [
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
    counts = Counter(row["counts"])
    unrun = sum(counts[kind] for kind in UNRUN)
    tests = f"{counts['passed']} / {counts['failed']} / {counts['no_fixture']} / {unrun}"
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
        "| Tool | Group | Outcome | Tests passed / failed / no fixture / not run "
        "| Implemented as | Upstream limitation |",
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
        f"{_named(combined, NOT_IMPLEMENTED, NOT_RUN, NO_TESTS)}.",
        f"Tests run and not passing: {_named(combined, FAIL, NO_FIXTURE, INCOMPLETE)}.",
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
    if call.excinfo is not None and isinstance(call.excinfo.value, UnmatchedUpstream):
        report.user_properties.append((UNMATCHED, call.excinfo.value.requests))
    marker = item.get_closest_marker("tool")
    tool = marker.args[0] if marker else None
    item.config.stash[COLLECTOR].record(report, tool, item.get_closest_marker("gate") is not None)
    return report


def write_report(config: pytest.Config, mode: str) -> None:
    """Write the run's report where `--report` says, if it says."""

    path = config.getoption("report")
    if path:
        Path(path).write_text(json.dumps(config.stash[COLLECTOR].report(mode), indent=2) + "\n")


def _read(path: Path, mode: str) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["mode"] != mode:
        raise SystemExit(f"{path} is the report of a {report['mode']} run, not of a {mode} run")
    return report


def main(arguments: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the per-tool acceptance report.")
    parser.add_argument("fixture", type=Path, help="the report of the fixture-mode run")
    parser.add_argument("--live", type=Path, help="the report of the live run")
    parser.add_argument("--limitations", type=Path, help="YAML: test id -> upstream requirement")
    options = parser.parse_args(arguments)
    fixture = _read(options.fixture, "fixture")
    if options.live:
        limitations = {}
        if options.limitations:
            limitations = yaml.safe_load(options.limitations.read_text(encoding="utf-8")) or {}
        combined = combine(fixture, _read(options.live, "live"), limitations)
        modes = "fixture and live"
    else:
        combined = {name: (row["outcome"], []) for name, row in fixture["tools"].items()}
        modes = "fixture only; the live run is not included, so no outcome here is §6's final one"
    sys.stdout.write(render(fixture, combined, modes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
