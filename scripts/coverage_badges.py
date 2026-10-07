"""Render public Shields endpoints from the two complete CI coverage reports."""

import json
import os
import sys
from pathlib import Path

MINIMUM = 90
AIM = 95


def coverage_color(percentage: float) -> str:
    """Use the project's aim and minimum, rather than color every measurement green."""
    if percentage > AIM:
        return "brightgreen"
    return "yellow" if percentage >= MINIMUM else "red"


def counts(totals: dict[str, object], kind: str) -> tuple[int, int]:
    """Reject missing or inconsistent counts rather than publish a reassuring badge."""
    total_key = "num_statements" if kind == "lines" else "num_branches"
    total, covered = totals[total_key], totals[f"covered_{kind}"]
    if type(total) is not int or type(covered) is not int:
        raise ValueError(f"Expected integer {kind} counts")
    if not 0 <= covered <= total:
        raise ValueError(f"Inconsistent {kind} counts")
    return covered, total


def badge(report: dict[str, object], label: str) -> dict[str, object]:
    metadata, totals = report["meta"], report["totals"]
    if not isinstance(metadata, dict) or metadata.get("branch_coverage") is not True:
        raise ValueError("Expected line and branch coverage")
    if not isinstance(totals, dict):
        raise ValueError("Expected coverage totals")
    lines, statements = counts(totals, "lines")
    branches, decisions = counts(totals, "branches")
    total, covered = statements + decisions, lines + branches
    if statements == 0:
        raise ValueError("Cannot badge an empty coverage report")
    percentage = 100 * covered / total
    # Match coverage.py: a rounded percentage must not hide a remaining gap.
    display = min(percentage, 99.99) if covered < total else percentage
    return {
        "schemaVersion": 1,
        "label": label,
        "message": f"{display:.2f}%",
        "color": coverage_color(percentage),
    }


def render(reports: Path, destination: Path, sha: str, run_url: str) -> None:
    # Validate both before writing anything: one successful suite is not a combined run.
    inputs = {
        name: json.loads((reports / f"{name}.json").read_text()) for name in ("server", "harness")
    }
    badges = {name: badge(report, f"{name} coverage") for name, report in inputs.items()}
    provenance = {
        "commit": sha,
        "run": run_url,
        "totals": {name: report["totals"] for name, report in inputs.items()},
    }
    destination.mkdir(parents=True, exist_ok=True)
    for name, document in (badges | {"provenance": provenance}).items():
        (destination / f"{name}.json").write_text(json.dumps(document, indent=2) + "\n")


def main() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    server = os.environ["GITHUB_SERVER_URL"]
    run = os.environ["GITHUB_RUN_ID"]
    render(
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        os.environ["GITHUB_SHA"],
        f"{server}/{repository}/actions/runs/{run}",
    )


if __name__ == "__main__":
    main()
