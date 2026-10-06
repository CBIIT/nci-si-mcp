"""Enforce the owner's zero High/Critical rule, including unfixable findings."""

import json
import sys
from pathlib import Path


def findings(report: dict) -> list[dict]:
    # Missing evidence must fail rather than look like a clean scan.
    if not report.get("Results"):
        raise ValueError("Scan has no package results")
    return [
        item
        for result in report["Results"]
        for item in result.get("Vulnerabilities", [])
        if item["Severity"] in {"HIGH", "CRITICAL"}
    ]


def main() -> int:
    blocked = findings(json.loads(Path(sys.argv[1]).read_text()))
    for item in blocked:
        print(f"{item['Severity']} {item['VulnerabilityID']} {item['PkgName']}")
    print(f"Publication gate: {len(blocked)} High/Critical findings (fixable or not)")
    return int(bool(blocked))


if __name__ == "__main__":
    raise SystemExit(main())
