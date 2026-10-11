"""Skip PR image builds only when every changed path is a known noninput."""

import re
import subprocess
import sys
from fnmatch import fnmatchcase

TESTS_AND_TEMPLATES = (
    "tests/*",
    ".github/ISSUE_TEMPLATE/*",
    ".github/PULL_REQUEST_TEMPLATE*",
)
# Acceptance self-tests are archived into Admin; AGENTS is a checked site link.
# README feeds installed wheel metadata, so it is deliberately absent from both lists.
# Unknown paths deliberately match neither list: new build inputs fail safe.
NONINPUTS = {
    "image": (
        *TESTS_AND_TEMPLATES,
        "acceptance/selftests/*",
        "AGENTS.md",
        "QUICKSTART.md",
        "CONTRIBUTING.md",
        "ARCHITECTURE.md",
        "docs/*.md",
    ),
    "companions": TESTS_AND_TEMPLATES,
}


def changed_paths(base: str, head: str) -> list[str]:
    if not all(re.fullmatch(r"[a-f0-9]{40}", value) for value in (base, head)):
        return []
    try:
        result = subprocess.run(  # noqa: S603 - validated SHAs, fixed Git arguments, no shell
            ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}", "--"],  # noqa: S607 - checkout provides Git
            capture_output=True,
            check=False,
            timeout=30,
        )
    except OSError, subprocess.TimeoutExpired:
        return []  # An unavailable diff cannot establish that skipping an image is safe.
    if result.returncode:
        return []
    return [
        path.decode("utf-8", errors="surrogateescape")
        for path in result.stdout.split(b"\0")
        if path
    ]


def main() -> None:
    event, base, head = sys.argv[1:]
    paths = changed_paths(base, head) if event == "pull_request" else []
    for job, patterns in NONINPUTS.items():
        selected = not paths or any(
            not any(fnmatchcase(path, pattern) for pattern in patterns) for path in paths
        )
        print(f"{job}={str(selected).lower()}")


if __name__ == "__main__":
    main()
