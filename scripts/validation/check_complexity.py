#!/usr/bin/env python3
"""Fail when a function or method has a cyclomatic complexity of 8 or more.

The count is radon's cyclomatic complexity, with assertions left out. Every
function and method is checked on its own, wherever it is defined: a function or a
class nested in another has its own count, and a class has none.

    python scripts/validation/check_complexity.py [path ...]

Without arguments the source package, the scripts, the tests and the acceptance suite are
checked.
"""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

from radon.visitors import ComplexityVisitor

Function = ast.FunctionDef | ast.AsyncFunctionDef

THRESHOLD = 8
DEFAULT_PATHS = ("src", "scripts", "tests", "acceptance")


def python_files(paths: list[str]) -> list[Path]:
    """The Python files named by the paths or below them; the default paths if none is given."""

    files: list[Path] = []
    for path in map(Path, paths or DEFAULT_PATHS):
        files += sorted(path.rglob("*.py")) if path.is_dir() else [path]
    return files


def _functions(node: ast.AST, scope: str = "") -> Iterator[tuple[str, Function]]:
    """Every function below the node with its qualified name, wherever it is defined."""

    for child in ast.iter_child_nodes(node):
        if isinstance(child, Function | ast.ClassDef):
            name = scope + child.name
            if isinstance(child, Function):
                yield name, child
            yield from _functions(child, f"{name}.")
        else:
            yield from _functions(child, scope)


def violations(path: Path) -> list[str]:
    """One line for each function or method of the file at or above the threshold."""

    found = []
    for name, function in _functions(ast.parse(path.read_text(encoding="utf-8"))):
        # radon counts a function without the functions and classes defined in it.
        complexity = ComplexityVisitor.from_ast(function, no_assert=True).functions[0].complexity
        if complexity >= THRESHOLD:
            found.append(f"{path}:{function.lineno}: {name} has complexity {complexity}")
    return found


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
