"""The per-tool report (docs/specification.md §5).

A run of the suite writes one report per run mode (`pytest --report=PATH`), with the
final outcome of every test, the transport of the run (stdio, or streamable-http for a remote
server) and the identity of the suite (suite_identity.py). A test counts for
the tool its `tool` marker names; a test marked `gate` gates every tool (§4). One run gives
each required tool one outcome:

    PASS             every test of the tool ran and passed, and every gate
    FAIL             a test of the tool failed, or a gate did (shown as "gates only")
    NO FIXTURE       the tool's tests failed only because a request found no fixture:
                     a question for the fixture set, not a defect of the server
    INCOMPLETE       the tool's tests that ran passed, but some could not run: skipped, or
                     needing a capability the tool lacks; a hardening
                     candidate, not a pass
    NOT IMPLEMENTED  the server does not expose the required tool name
    NOT RUN          no test of the tool ran (in live mode: none is live-capable)
    NO TESTS         the suite has no test for the tool: a defect of the suite

In a live run, the tests that run in fixture mode only leave a tool NOT RUN, never INCOMPLETE.

Combining the fixture run with the live run gives the final outcome. A tool that
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

from nci_si_acceptance.client import withhold_authorization
from nci_si_acceptance.spec import REQUIRED_TOOLS
from nci_si_acceptance.suite import FIXTURE_ONLY as FIXTURE_ONLY_SKIP
from nci_si_acceptance.suite import UnmatchedUpstream
from nci_si_acceptance.suite_identity import (
    APPROVED,
    REPOSITORY,
    SuiteError,
    approved_digests,
    heading,
    identity,
)
from nci_si_acceptance.tools import NOT_IMPLEMENTED

if TYPE_CHECKING:
    from collections.abc import Iterable

    from nci_si_acceptance.tools import Tools

PASS, FAIL, NO_FIXTURE = "PASS", "FAIL", "NO FIXTURE"
INCOMPLETE, NOT_RUN, NO_TESTS = "INCOMPLETE", "NOT RUN", "NO TESTS"
FIXTURE_ONLY = "PASS (fixture only)"
# The property a test failing for want of fixtures carries: the requests concerned.
UNMATCHED = "unmatched_upstream"
# The property that carries a test's tool and gate to wherever the report is collected.
ATTRIBUTION = "acceptance_attribution"
# The key of a worker's output that carries what it noted of the server under test.
WORKER_TOOLS = "acceptance_tools"
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
        # Which required tools the server lists; None until a server has started.
        self.implemented: dict[str, bool] | None = None
        self.listing_bytes: int | None = None
        # Set from the target at the end of the run; the default is the harness's original one.
        self.transport = "stdio"

    # The collector is itself a plugin, so that it collects in the process that writes the
    # report: under xdist, the controller, which sees the workers' reports here and nothing
    # of their tests, and their notes of the server when they finish.

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.record(report)

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        output = getattr(session.config, "workeroutput", None)  # set on an xdist worker only
        if (noted := self.noted_tools()) is not None and output is not None:
            output[WORKER_TOOLS] = noted

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: Any) -> None:
        if noted := getattr(node, "workeroutput", {}).get(WORKER_TOOLS):
            self.merge_tools(noted)

    def record(self, report: pytest.TestReport) -> None:
        """Take one phase of a test; its tool and gate come in the report's own properties,
        which is all that reaches the controller process of a run on xdist workers."""

        outcome = _phase_outcome(report)
        if outcome is None:
            return
        properties: dict[str, Any] = dict(report.user_properties)
        attribution = properties.get(ATTRIBUTION) or {"tool": None, "gate": False}
        test = self.tests.setdefault(report.nodeid, {**attribution, "outcome": outcome})
        if RANK[outcome] >= RANK[test["outcome"]]:
            test["outcome"] = outcome
            test["unmatched"] = properties.get(UNMATCHED, [])

    def note_tools(self, tools: Tools) -> None:
        self.implemented = {name: tools.implemented(name) for name in REQUIRED_TOOLS}
        self.listing_bytes = tools.listing_bytes

    def noted_tools(self) -> dict[str, Any] | None:
        """What a worker noted of its server, to send to the controller; None if no server."""

        if self.implemented is None:
            return None
        return {"implemented": self.implemented, "listing_bytes": self.listing_bytes}

    def merge_tools(self, noted: dict[str, Any]) -> None:
        """Take a worker's notes of its server, unless an earlier worker's are already here."""

        if self.implemented is None:
            self.implemented = noted["implemented"]
            self.listing_bytes = noted["listing_bytes"]

    def _row(self, name: str, group: str, gates_failed: bool) -> dict[str, Any]:
        counts = Counter(test["outcome"] for test in self.tests.values() if test["tool"] == name)
        implemented = None if self.implemented is None else self.implemented.get(name, False)
        return {
            "group": group,
            "outcome": tool_outcome(counts, gates_failed, implemented),
            "gates_only": gates_failed and not (counts["failed"] or counts["no_fixture"]),
            "counts": dict(counts),
        }

    def _gates(self, outcomes: tuple[str, ...]) -> list[str]:
        return sorted(
            nodeid
            for nodeid, test in self.tests.items()
            if test["gate"] and test["outcome"] in outcomes
        )

    def report(self, mode: str) -> dict[str, Any]:
        gates = self._gates(FAILED)
        rows = {name: self._row(name, group, bool(gates)) for name, group in REQUIRED_TOOLS.items()}
        return {
            "mode": mode,
            "transport": self.transport,
            "failed_gates": gates,
            "unrun_gates": self._gates(UNRUN),
            "tools_list_bytes": self.listing_bytes,
            "tools": rows,
            # Workers report in the order they finish; a report is the same whatever that was.
            "tests": dict(sorted(self.tests.items())),
        }


def combine(
    fixture: dict[str, Any], live: dict[str, Any], limitations: dict[str, str]
) -> dict[str, tuple[str, list[str]]]:
    """Each tool's outcome over both run modes, with the limitations that excuse it."""

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
    return f"| `{name}` | {row['group']} | {outcome} | {tests} | {', '.join(excused)} |"


def _named(combined: dict[str, tuple[str, list[str]]], *kinds: str) -> str:
    return ", ".join(name for name, (outcome, _) in combined.items() if outcome in kinds) or "none"


def _size(size: int | None) -> str:
    return "not measured, no server started" if size is None else f"{size:,} bytes"


def render(
    report: dict[str, Any],
    combined: dict[str, tuple[str, list[str]]],
    modes: str,
    approved: Path = APPROVED,
) -> str:
    """The report as Markdown: the suite's identity, which says whether this is an acceptance
    report or MODIFIED (suite_identity.py), then the per-tool table and the tools whose tests
    prove nothing yet."""

    lines = [
        *heading(report["suite"], approved_digests(approved)),
        f"Run modes: {modes}.",
        "",
        "| Tool | Group | Outcome | Tests passed / failed / no fixture / not run "
        "| Upstream limitation |",
        "|---|---|---|---|---|",
    ]
    lines += [_row(name, row, *combined[name]) for name, row in report["tools"].items()]
    missing = sorted(
        {request for test in report["tests"].values() for request in test.get("unmatched", [])}
    )
    lines += [
        "",
        f"Gates failed: {', '.join(report['failed_gates']) or 'none'}.",
        f"Gates not run: {', '.join(report['unrun_gates']) or 'none'}.",
        "Tests never run against an implementation: "
        f"{_named(combined, NOT_IMPLEMENTED, NOT_RUN, NO_TESTS)}.",
        f"Tests run and not passing: {_named(combined, FAIL, NO_FIXTURE, INCOMPLETE)}.",
        f"Requests without a fixture: {'; '.join(missing) or 'none'}.",
        f"Size of the tools/list result: {_size(report['tools_list_bytes'])}.",
    ]
    return "\n".join(lines) + "\n"


# ---- pytest plugin

COLLECTOR = pytest.StashKey[Collector]()
SUITE = pytest.StashKey[dict[str, str]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--report", metavar="PATH", help="write the per-tool report of the run as JSON"
    )


def pytest_configure(config: pytest.Config) -> None:
    config.stash[COLLECTOR] = Collector()
    config.pluginmanager.register(config.stash[COLLECTOR], "acceptance-collector")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    report = yield
    withhold_from(report)
    if call.excinfo is not None and isinstance(call.excinfo.value, UnmatchedUpstream):
        report.user_properties.append((UNMATCHED, call.excinfo.value.requests))
    marker = item.get_closest_marker("tool")
    attribution = {
        "tool": marker.args[0] if marker else None,
        "gate": item.get_closest_marker("gate") is not None,
    }
    report.user_properties.append((ATTRIBUTION, attribution))
    return report


def withhold_from(report: pytest.TestReport) -> None:
    """Keep the operator's credential out of a failure's text and captured output, where a
    server that echoes it back would otherwise show it."""

    if report.longrepr is not None and not isinstance(report.longrepr, tuple):
        shown = str(report.longrepr)
        if (clean := withhold_authorization(shown)) != shown:
            report.longrepr = clean
    report.sections = [(name, withhold_authorization(text)) for name, text in report.sections]


def suite_state(config: pytest.Config) -> dict[str, str]:
    """The identity of the suite tree this run is in: the repository around the acceptance
    directory pytest runs in. It must be the tree the harness is installed from, or the report
    would name the harness's files for the tests that ran."""

    root = config.rootpath.parent
    if root != REPOSITORY:
        raise pytest.UsageError(
            f"this run is in the suite at {root}, but the harness is installed from {REPOSITORY}; "
            "install the harness from the checkout under test (pdm install)"
        )
    try:
        return identity(root)
    except SuiteError as error:
        raise pytest.UsageError(str(error)) from error


def pytest_sessionstart(session: pytest.Session) -> None:
    """Take the suite's identity before any test runs: it is the tree that ran."""

    if session.config.getoption("report"):
        session.config.stash[SUITE] = suite_state(session.config)


def write_report(config: pytest.Config, mode: str) -> None:
    """Write the run's report where `--report` says, if it says."""

    path = config.getoption("report")
    # A worker holds part of the run; the controller writes the report.
    if path and not hasattr(config, "workerinput"):
        report = config.stash[COLLECTOR].report(mode) | {"suite": config.stash[SUITE]}
        Path(path).write_text(json.dumps(withheld(report), indent=2) + "\n")


def withheld(value: Any) -> Any:
    """`value` with the operator's credential out of every string in it: before it is written as
    JSON, where a credential with quotes, backslashes or non-ASCII characters would be escaped
    and so no longer found."""

    if isinstance(value, str):
        return withhold_authorization(value)
    if isinstance(value, dict):
        return {key: withheld(item) for key, item in value.items()}
    if isinstance(value, list):
        return [withheld(item) for item in value]
    return value


def _read(path: Path, mode: str) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if "transport" not in report:
        raise SystemExit(f"{path} was written by an older suite; re-run it")
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
        live = _read(options.live, "live")
        if live["suite"] != fixture["suite"]:
            raise SystemExit("the fixture and live reports come from different suites")
        limitations = {}
        if options.limitations:
            limitations = yaml.safe_load(options.limitations.read_text(encoding="utf-8")) or {}
        combined = combine(fixture, live, limitations)
        modes = f"fixture ({fixture['transport']}) and live ({live['transport']})"
    else:
        combined = {name: (row["outcome"], []) for name, row in fixture["tools"].items()}
        modes = (
            f"fixture ({fixture['transport']}) only; "
            "the live run is not included, so no outcome here is final"
        )
    sys.stdout.write(render(fixture, combined, modes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
