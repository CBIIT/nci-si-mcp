"""Render pytest JUnit counts and failed identities, never diagnostic bodies."""

import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


def outcome(case: ET.Element) -> str:
    for tag in ("error", "failure", "skipped"):
        if case.find(tag) is not None:
            return tag
    return "passed"


def render(path: Path, title: str) -> str:
    # This report is produced by the job's own pytest, not accepted from a remote caller.
    root = ET.parse(path).getroot()  # noqa: S314 - owned pytest artifact.
    cases = list(root.iter("testcase"))
    if not cases:
        raise ValueError("No JUnit testcases")
    counts = Counter(outcome(case) for case in cases)
    lines = [
        f"## {title}",
        "",
        "| Total | Passed | Failed | Errors | Skipped |",
        "| --- | --- | --- | --- | --- |",
        f"| {len(cases)} | {counts['passed']} | {counts['failure']} | "
        f"{counts['error']} | {counts['skipped']} |",
        "",
    ]
    failures = []
    for case in cases:
        kind = outcome(case)
        if kind not in {"error", "failure"}:
            continue
        identity = "::".join(filter(None, (case.get("classname"), case.get("name"))))
        failures.append(f"- {kind}: {identity}")
    if failures:
        lines.extend(["### Failed tests", "", *failures])
    return "\n".join(lines) + "\n"


def main() -> int:
    try:
        summary = render(Path(sys.argv[1]), sys.argv[2])
    except OSError, ET.ParseError, ValueError:
        print(
            "JUnit report unavailable or invalid; inspect the test step and XML artifact.",
            file=sys.stderr,
        )
        return 1
    print(summary, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
