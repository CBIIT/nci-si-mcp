"""Refresh dependencies under the rolling age policy and security-fix cutoffs."""

import subprocess
import sys
from pathlib import Path

from scripts.dependency_age import (
    ROOT,
    Fetch,
    Package,
    fetch_json,
    read_exceptions,
    release_upload_time,
)


def command(root: Path = ROOT, fetch: Fetch = fetch_json) -> list[str]:
    arguments = ["pdm", "lock", "--exclude-newer", "5d"]
    for name, exception in sorted(read_exceptions(root).items()):
        uploaded = release_upload_time(Package("pypi", name, exception.version), fetch)
        arguments.extend(["--exclude-newer-override", f"{name}={uploaded.isoformat()}"])
    return arguments


def main() -> int:
    try:
        arguments = command()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    return subprocess.run(arguments, cwd=ROOT, check=False).returncode  # noqa: S603


if __name__ == "__main__":
    raise SystemExit(main())
