#!/usr/bin/env python3
"""Fail when a function or method has a cyclomatic complexity of 8 or more.

The count is radon's cyclomatic complexity, with assertions left out. A class has
no count of its own; its methods are checked one by one, and so are the functions
and classes defined inside a function or a class.

    python scripts/validation/check_complexity.py [path ...]

Without arguments the source package, the scripts and the tests are checked.
"""

import sys
from collections.abc import Iterator
from pathlib import Path

from radon.visitors import Class, ComplexityVisitor, Function

THRESHOLD = 8
DEFAULT_PATHS = ("src", "scripts", "tests")


def python_files(paths: list[str]) -> list[Path]:
    """The Python files named by the paths or below them; the default paths if none is given."""

    files: list[Path] = []
    for path in map(Path, paths or DEFAULT_PATHS):
        files += sorted(path.rglob("*.py")) if path.is_dir() else [path]
    return files


def _functions(blocks: list[Function | Class], scope: str = "") -> Iterator[tuple[str, Function]]:
    """Every function with its qualified name, at any depth of nesting."""

    for block in blocks:
        name = scope + block.name
        if isinstance(block, Class):
            yield from _functions([*block.methods, *block.inner_classes], f"{name}.")
        else:
            yield name, block
            yield from _functions(block.closures, f"{name}.")


def violations(path: Path) -> list[str]:
    """One line for each function or method of the file at or above the threshold."""

    visitor = ComplexityVisitor.from_code(path.read_text(encoding="utf-8"), no_assert=True)
    return [
        f"{path}:{function.lineno}: {name} has complexity {function.complexity}"
        for name, function in _functions([*visitor.functions, *visitor.classes])
        if function.complexity >= THRESHOLD
    ]


def main(arguments: list[str]) -> int:
    found = [line for path in python_files(arguments) for line in violations(path)]
    if not found:
        return 0
    print(f"Cyclomatic complexity of {THRESHOLD} or more:")
    print("\n".join(found))
    print("Split the function: extract a helper, use a table, or return early.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
