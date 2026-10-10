"""The expected outcomes of the fixture-mode run: the ratchet CI holds the server to.

    pdm run acceptance-expected check acceptance/fixture.json
    pdm run acceptance-expected update acceptance/fixture.json

`acceptance/expected/fixture.json` maps the id of every test to its outcome in the report's own
vocabulary (report.py), and holds nothing else, so a reworded failure is not a change. `check`
compares a report with it and exits with 1 on any difference: a test with another outcome, a
test the report lacks, a test the expected outcomes lack. `update` rewrites the file from a
report, for a change that moves outcomes on purpose; the diff of the file is then what the
review reads.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nci_si_acceptance.record import FIXTURES
from nci_si_acceptance.report import load_report

if TYPE_CHECKING:
    from collections.abc import Iterable

EXPECTED = FIXTURES.parent / "expected" / "fixture.json"
ABSENT_FROM_REPORT = "(not in the report)"
ABSENT_FROM_EXPECTED = "(not in the expected outcomes)"
HINT = (
    "If the change is on purpose, run `pdm run acceptance-expected update "
    "acceptance/fixture.json` on the report of a fixture-mode run and commit "
    "`acceptance/expected/fixture.json`."
)


def outcomes_of(report: dict[str, Any]) -> dict[str, str]:
    """The outcome of every test of a fixture-mode report, by test id."""

    return {nodeid: test["outcome"] for nodeid, test in sorted(report["tests"].items())}


def differences(expected: dict[str, str], actual: dict[str, str]) -> list[tuple[str, str, str]]:
    """Each test whose outcome differs, with the one expected and the one found, in test order."""

    return [
        (nodeid, expected.get(nodeid, ABSENT_FROM_EXPECTED), actual.get(nodeid, ABSENT_FROM_REPORT))
        for nodeid in sorted(expected.keys() | actual.keys())
        if expected.get(nodeid) != actual.get(nodeid)
    ]


def _cell(text: str) -> str:
    return "`" + text.replace("|", "\\|") + "`"


def render(rows: Iterable[tuple[str, str, str]]) -> str:
    """The differences as Markdown: a table and the hint on how to accept them."""

    lines = ["| Test | Expected | Actual |", "|---|---|---|"]
    lines += [f"| {_cell(test)} | {expected} | {actual} |" for test, expected, actual in rows]
    return "\n".join(lines) + "\n"


def check(expected_path: Path, report_path: Path) -> tuple[int, str]:
    """The exit status and the Markdown that compare a report with the expected outcomes."""

    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    actual = outcomes_of(load_report(report_path, "fixture"))
    found = differences(expected, actual)
    if not found:
        return 0, f"All {len(actual)} tests have the outcome expected.\n"
    heading = f"{len(found)} of {len(actual | expected)} tests differ from the expected outcomes.\n"
    return 1, f"{heading}\n{render(found)}\n{HINT}\n"


def update(expected_path: Path, report_path: Path) -> int:
    """Rewrite the expected outcomes from a report; the number of tests written."""

    written = outcomes_of(load_report(report_path, "fixture"))
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    expected_path.write_text(json.dumps(written, indent=2) + "\n", encoding="utf-8")
    return len(written)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare a report with the expected outcomes.")
    parser.add_argument("command", choices=["check", "update"])
    parser.add_argument("report", type=Path, help="the report of a fixture-mode run")
    parser.add_argument("--expected", type=Path, default=EXPECTED, help="the expected outcomes")
    options = parser.parse_args(arguments)
    if options.command == "update":
        count = update(options.expected, options.report)
        sys.stdout.write(f"Wrote the outcomes of {count} tests to {options.expected}.\n")
        return 0
    status, text = check(options.expected, options.report)
    sys.stdout.write(text)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
