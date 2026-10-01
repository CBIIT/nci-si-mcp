#!/usr/bin/env python3
"""Fail when a function or method has a cyclomatic complexity of 8 or more.

The count is radon's: one, plus one for each branch, loop, exception handler,
boolean operator and comprehension. Assertions are not counted. A class has no
count of its own here; its methods are checked one by one.

    python scripts/validation/check_complexity.py [path ...]

Without arguments the source package and the scripts are checked.
"""

import sys
from pathlib import Path

from radon.complexity import cc_visit
from radon.visitors import Class

THRESHOLD = 8
DEFAULT_PATHS = ("src", "scripts")


def python_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for path in map(Path, paths):
        files += sorted(path.rglob("*.py")) if path.is_dir() else [path]
    return files


def violations(path: Path) -> list[str]:
    """One line for each function or method of the file at or above the threshold."""

    blocks = cc_visit(path.read_text(encoding="utf-8"), no_assert=True)
    return [
        f"{path}:{block.lineno}: {block.fullname} has complexity {block.complexity}"
        for block in blocks
        if not isinstance(block, Class) and block.complexity >= THRESHOLD
    ]


def main(arguments: list[str]) -> int:
    found = [
        line for path in python_files(arguments or list(DEFAULT_PATHS)) for line in violations(path)
    ]
    if not found:
        return 0
    print(f"Cyclomatic complexity of {THRESHOLD} or more:")
    print("\n".join(found))
    print("Split the function: extract a helper, use a table, or return early.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
