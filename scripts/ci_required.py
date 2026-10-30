"""Require success from every CI job except the explicitly conditional jobs."""

import json
import os
import sys

ALLOWED_SKIPS = frozenset({"mutation", "image", "companions", "coverage-badges"})


def job_result(name: str, row: object) -> str:
    if not isinstance(row, dict):
        raise ValueError(f"{name} must be a job object")
    result = row.get("result")
    if not isinstance(result, str):
        raise ValueError(f"{name} has a non-string result")
    return result


def results(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ValueError("expected a nonempty object")
    found: dict[str, str] = {}
    for name, row in value.items():
        found[name] = job_result(name, row)
    return found


def main() -> int:
    try:
        needs = results(json.loads(os.environ.get("NEEDS_JSON", "")))
    except ValueError as error:
        print(f"Invalid CI needs: {error}", file=sys.stderr)
        return 1
    failed = [
        name
        for name, result in needs.items()
        if result != "success" and not (result == "skipped" and name in ALLOWED_SKIPS)
    ]
    for name in failed:
        print(f"CI job {name}: {needs[name]!r} is not an allowed result", file=sys.stderr)
    return int(bool(failed))


if __name__ == "__main__":
    sys.exit(main())
