"""Capture the original case inventory, story mapping and selection before worker execution."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from scripts.benchmark import CASES, cases
from scripts.benchmark_http import conditions
from scripts.benchmark_limits import Limits
from scripts.evidence_http import http_fingerprint

from nci_si_acceptance.spec import REQUIRED_TOOLS
from nci_si_acceptance.stories import ROOT, Collection, cases_of, collect, load_stories, validate
from nci_si_acceptance.suite_identity import suite_digest


class Inventory(Collection):
    def __init__(self) -> None:
        super().__init__()
        self.attribution: dict[str, dict[str, Any]] = {}

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        super().pytest_collection_finish(session)
        for item in session.items:
            marker = item.get_closest_marker("tool")
            case = cases_of([item])[0]
            self.attribution[case.key] = {
                "tool": marker.args[0] if marker else None,
                "gate": item.get_closest_marker("gate") is not None,
            }


def benchmark_selection(mode: str) -> dict[str, Any]:
    selected = cases(list(CASES))
    planned = {
        "mode": mode,
        "transport": "streamable-http",
        "conditions": conditions(),
        "limits": asdict(Limits(max_requests=500)),
        "cases": selected,
    }
    return {"schema": 1, "cases": selected, "fingerprint": http_fingerprint(planned)}


def snapshots(inventory: Inventory, kind: str, mode: str) -> dict[str, Any]:
    stories = load_stories()
    validate(stories, inventory.cases)
    mapping = {function: story["id"] for story in stories for function in story["tests"]}
    return {
        "catalogue": {
            "schema": 1,
            "suite_digest": suite_digest(),
            "tools": REQUIRED_TOOLS,
            "cases": inventory.attribution,
        },
        "stories": {case.key: mapping[case.function] for case in inventory.cases},
        "expectations": json.loads((ROOT / "expected/fixture.json").read_text()),
        "selection": (
            [case.key for case in inventory.cases]
            if kind == "acceptance"
            else benchmark_selection(mode)
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", choices=("acceptance", "benchmark"), required=True)
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    args = parser.parse_args()
    inventory = Inventory()
    if status := collect(inventory):
        return status
    original = snapshots(inventory, args.kind, args.mode)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in original.items():
        (args.output / f"{name}.json").write_text(json.dumps(value, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
