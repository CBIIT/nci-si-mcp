"""Refuse a manual release unless the newest CI run validated its exact main SHA."""

import json
import os
import re
import shutil
import subprocess
import sys


def matching_run(value: object, sha: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("CI run must be an object")
    expected = {
        "head_sha": sha,
        "head_branch": "main",
        "event": "push",
        "path": ".github/workflows/ci.yml",
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise ValueError("CI run identity does not match the main commit")
    identifier = value.get("id")
    if type(identifier) is not int or identifier <= 0:
        raise ValueError("CI run has no valid identifier")
    return {str(key): item for key, item in value.items()}


def require_success(response: object, sha: str) -> None:
    if not isinstance(response, dict):
        raise ValueError("CI response must be an object")
    rows = response.get("workflow_runs")
    if not isinstance(rows, list) or not rows:
        raise ValueError("No CI run for the main commit")
    runs = [matching_run(row, sha) for row in rows]
    # GitHub run IDs increase across runs; an API-order change must not pick an older success.
    latest = max(runs, key=lambda row: int(str(row["id"])))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValueError("Newest CI run has not completed successfully")


def main() -> int:
    sha = sys.argv[1]
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        print("CI validation requires an exact commit SHA", file=sys.stderr)
        return 1
    repository = os.environ["GITHUB_REPOSITORY"]
    route = (
        f"repos/{repository}/actions/workflows/ci.yml/runs"
        f"?head_sha={sha}&branch=main&event=push&per_page=100"
    )
    try:
        output = subprocess.check_output(  # noqa: S603 - fixed gh command, no shell
            [shutil.which("gh") or "gh", "api", route], text=True, stderr=subprocess.PIPE
        )
        require_success(json.loads(output), sha)
    except (ValueError, subprocess.CalledProcessError) as error:
        # Never print an upstream body, command output or credential.
        reason = str(error) if isinstance(error, ValueError) else "CI API request failed"
        print(
            f"Release refused: {reason}. Require successful CI for this main SHA.", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
