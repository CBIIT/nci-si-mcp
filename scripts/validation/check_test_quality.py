#!/usr/bin/env python3
"""Fail on tests that cannot catch a regression.

A test function (`test_*`) is rejected when it

- asserts nothing,
- asserts only how a mock was used (`assert_called*`, `call_count`, ...), or
- asserts only that something is callable.

A test module is rejected when its docstring states a coverage aim ("improve
coverage", "coverage to 95%"): a test exists for the behaviour it protects, not
for a number.

The tests are `unittest` style. An assertion is an `assert` statement, a
`unittest` assertion method (`self.assertEqual`, `self.assertRaises`,
`self.fail`, ...), or a call to a helper whose name starts with `assert_` and is
not a mock assertion. An assertion inside a function that the test defines and
never refers to by name does not count, nor does one in a method of a class that
the test defines: the test itself has to assert.

    python scripts/validation/check_test_quality.py tests/test_a.py [...]
"""

import ast
import re
import sys
from collections.abc import Iterator
from pathlib import Path

TestFunction = ast.FunctionDef | ast.AsyncFunctionDef

COVERAGE_AIM = re.compile(
    r"(improve|increase|raise)s?\s+(the\s+)?coverage|coverage\s+to\s+\d+\s*%", re.IGNORECASE
)
# `assert`, a unittest method such as assertEqual or fail, or a helper such as assert_valid.
ASSERTION = re.compile(r"assert|assert[A-Z]\w*|fail|_?assert_\w+")
MOCK_ASSERTION = re.compile(
    r"assert_(not_)?(called|awaited)\w*|assert_any_(call|await)|assert_has_(calls|awaits)"
)
WEAK = {"mock": "how a mock was used", "callable": "that something is callable"}
MOCK_STATE = {
    "called",
    "call_count",
    "call_args",
    "call_args_list",
    "mock_calls",
    "method_calls",
    "await_count",
    "await_args",
    "await_args_list",
}


def _called_name(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Attribute):
        return function.attr
    return function.id if isinstance(function, ast.Name) else ""


def _reads_mock_state(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Attribute) and child.attr in MOCK_STATE for child in ast.walk(node)
    )


def _is_callable_check(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Call) and _called_name(child) == "callable"
        for child in ast.walk(node)
    )


def _kind(node: ast.AST) -> str | None:
    """Classify a node as a `mock`, `callable` or `behaviour` assertion, or None."""

    if isinstance(node, ast.Assert):
        subject, name = node.test, "assert"
    elif isinstance(node, ast.Call):
        subject, name = node, _called_name(node)
    else:
        return None
    if MOCK_ASSERTION.fullmatch(name):
        return "mock"
    if not ASSERTION.fullmatch(name):
        return None
    if _reads_mock_state(subject):
        return "mock"
    return "callable" if _is_callable_check(subject) else "behaviour"


def _nodes_that_run(test: TestFunction) -> Iterator[ast.AST]:
    """The nodes of a test, without the functions it defines and never names again."""

    named = {node.id for node in ast.walk(test) if isinstance(node, ast.Name)}
    pending = list(ast.iter_child_nodes(test))
    while pending:
        node = pending.pop()
        if isinstance(node, TestFunction) and node.name not in named:
            continue
        yield node
        pending.extend(ast.iter_child_nodes(node))


def _finding(test: TestFunction) -> str | None:
    kinds = {kind for node in _nodes_that_run(test) if (kind := _kind(node))}
    if "behaviour" in kinds:
        return None
    if not kinds:
        return "asserts nothing"
    weak = " and ".join(WEAK[kind] for kind in sorted(kinds))
    return f"only asserts {weak}; assert the result or the effect as well"


def findings(path: Path) -> list[str]:
    """The findings of one test file, each as `path:line: message`."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    if COVERAGE_AIM.search(ast.get_docstring(tree) or ""):
        found.append(f"{path}:1: the module docstring states a coverage aim")
    for node in ast.walk(tree):
        if isinstance(node, TestFunction) and node.name.startswith("test_"):
            problem = _finding(node)
            if problem:
                found.append(f"{path}:{node.lineno}: {node.name} {problem}")
    return found


def main(arguments: list[str]) -> int:
    found = [line for argument in arguments for line in findings(Path(argument))]
    if not found:
        return 0
    print("Tests that cannot catch a regression:")
    print("\n".join(found))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
