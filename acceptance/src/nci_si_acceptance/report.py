"""The per-tool report (MCP Behavioral Acceptance Suite §6).

A run of the suite writes one report per run mode (`pytest --report=PATH`), with the
outcome of every test. A test counts for the tool its `tool` marker names; a test
marked `gate` gates every tool (§3). One run gives each required tool one outcome:

    PASS             every test of the tool passed, and every gate
    FAIL             a test of the tool, or a gate, failed
    NOT IMPLEMENTED  the server has the tool neither by name nor through the tool map
    NOT RUN          every test of the tool was skipped for another reason (live mode)
    NO TESTS         the suite has no test for the tool: a defect of the suite

Combining the fixture run with the live run gives the outcome of §6: a tool that
passes against fixtures and fails live is PASS (fixture only) when a documented
upstream limitation explains it, and FAIL otherwise.

    python -m nci_si_acceptance.report fixture.json [--live live.json] [--limitations FILE]

PYTEST_DONT_REWRITE: the suite's conftest imports this plugin before pytest could
rewrite its assertions, and it has none.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from nci_si_acceptance.inventory import REQUIRED_TOOLS
from nci_si_acceptance.tools import NOT_IMPLEMENTED

if TYPE_CHECKING:
    from collections.abc import Iterable

    from nci_si_acceptance.tools import Tools

PASS, FAIL = "PASS", "FAIL"
NOT_RUN, NO_TESTS = "NOT RUN", "NO TESTS"
FIXTURE_ONLY = "PASS (fixture only)"


def tool_outcome(tests: dict[str, int], gates_failed: bool) -> str:
    """The outcome of one tool in one run, from the count of its test outcomes."""

    if not any(tests.values()):
        return NO_TESTS
    if tests["failed"] or (gates_failed and tests["passed"]):
        return FAIL
    if tests["passed"]:
        return PASS
    return NOT_IMPLEMENTED if tests["not_implemented"] else NOT_RUN


def _test_outcome(report: pytest.TestReport) -> str | None:
    if report.failed:
        return "failed"
    if report.skipped:
        reason = str(report.longrepr[2]) if isinstance(report.longrepr, tuple) else ""
        return "not_implemented" if NOT_IMPLEMENTED in reason else "skipped"
    return "passed" if report.when == "call" else None


class Collector:
    """Counts the test outcomes of a run per tool, and the failed gates."""

    def __init__(self) -> None:
        self.tests: dict[str, dict[str, int]] = defaultdict(
            lambda: {"passed": 0, "failed": 0, "not_implemented": 0, "skipped": 0}
        )
        self.failed_gates: list[str] = []
        self.implemented_as: dict[str, str | None] = {}
        self.outcomes: dict[str, str] = {}

    def record(self, report: pytest.TestReport, tool: str | None, gate: bool) -> None:
        outcome = _test_outcome(report)
        if outcome is None or self.outcomes.get(report.nodeid) == "failed":
            return
        self.outcomes[report.nodeid] = outcome
        if gate and outcome == "failed":
            self.failed_gates.append(report.nodeid)
        if tool is not None:
            self.tests[tool][outcome] += 1

    def note_tools(self, tools: Tools) -> None:
        self.implemented_as = {name: tools.implemented_as(name) for name in REQUIRED_TOOLS}

    def report(self, mode: str) -> dict[str, Any]:
        rows = {}
        for name, group in REQUIRED_TOOLS.items():
            tests = self.tests[name]
            rows[name] = {
                "group": group,
                "outcome": tool_outcome(tests, bool(self.failed_gates)),
                "implemented_as": self.implemented_as.get(name),
                "tests": tests,
            }
        return {
            "mode": mode,
            "failed_gates": self.failed_gates,
            "tools": rows,
            "tests": self.outcomes,
        }


def combine(
    fixture: dict[str, Any], live: dict[str, Any], limitations: dict[str, str]
) -> dict[str, str]:
    """The outcome of each tool over both run modes (§6)."""

    outcomes = {}
    for name, row in fixture["tools"].items():
        outcome, live_outcome = row["outcome"], live["tools"][name]["outcome"]
        if outcome == PASS and live_outcome == FAIL:
            outcome = FIXTURE_ONLY if name in limitations else FAIL
        outcomes[name] = outcome
    return outcomes


def _row(name: str, row: dict[str, Any], outcome: str, limitation: str) -> str:
    stand_in = row["implemented_as"] or "—"
    return f"| `{name}` | {row['group']} | {outcome} | {stand_in} | {limitation} |"


def _named(outcomes: dict[str, str], *kinds: str) -> str:
    return ", ".join(name for name, outcome in outcomes.items() if outcome in kinds) or "none"


def render(report: dict[str, Any], outcomes: dict[str, str], limitations: dict[str, str]) -> str:
    """The report as a Markdown table, with the tools whose tests prove nothing yet."""

    lines = [
        "| Tool | Group | Outcome | Implemented as | Upstream limitation |",
        "|---|---|---|---|---|",
    ]
    lines += [
        _row(name, row, outcomes[name], limitations.get(name, ""))
        for name, row in report["tools"].items()
    ]
    lines += [
        "",
        f"Gates failed: {', '.join(report['failed_gates']) or 'none'}.",
        "Tests never run against an implementation: "
        f"{_named(outcomes, NOT_IMPLEMENTED, NO_TESTS)}.",
        f"Tests run and not passing: {_named(outcomes, FAIL)}.",
    ]
    return "\n".join(lines) + "\n"


# ---- pytest plugin


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--report", metavar="PATH", help="write the per-tool report of the run as JSON"
    )


COLLECTOR = pytest.StashKey[Collector]()


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
    parser.add_argument("--limitations", type=Path, help="YAML: tool -> upstream requirement")
    options = parser.parse_args(arguments)
    fixture = json.loads(options.fixture.read_text(encoding="utf-8"))
    limitations = (
        yaml.safe_load(options.limitations.read_text(encoding="utf-8"))
        if options.limitations
        else {}
    )
    if options.live:
        outcomes = combine(
            fixture, json.loads(options.live.read_text(encoding="utf-8")), limitations
        )
    else:
        outcomes = {name: row["outcome"] for name, row in fixture["tools"].items()}
    sys.stdout.write(render(fixture, outcomes, limitations or {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
