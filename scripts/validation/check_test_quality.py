#!/usr/bin/env python3
"""Fail on tests that cannot catch a regression.

A test function (`test_*`) is rejected when it

- asserts nothing,
- asserts only how a mock was used (`assert_called*`, `call_count`, ...), or
- asserts only that something is callable.

A test module is rejected when its docstring states a coverage aim ("improve
coverage", "coverage to 95%", "95% coverage"): a test exists for the behaviour
it protects, not for a number.

An assertion is an `assert` statement, a `unittest` assertion method
(`self.assertEqual`, `self.assertRaises`, `self.fail`, ...), `pytest.raises`, or a
call to a helper whose name starts with `assert_` and is not a mock assertion. An
assertion inside a function or a class that the test defines and then never uses
does not count: it never runs. A definition counts as used when the test reads its
name or it carries a decorator.

    python scripts/validation/check_test_quality.py tests/test_a.py [...]
"""

import ast
import re
import sys
from collections.abc import Iterator
from pathlib import Path

TestFunction = ast.FunctionDef | ast.AsyncFunctionDef
Definition = TestFunction | ast.ClassDef

COVERAGE_AIM = re.compile(
    r"\b(improv|increas|rais|boost)\w*\s+(the\s+)?(\w+\s+)?coverage\b"
    r"|coverage\s+to\s+\d+\s*%|\d+\s*%\s+coverage\b",
    re.IGNORECASE,
)
# `assert`, a unittest method such as assertEqual or fail, pytest.raises, or a helper such
# as assert_valid.
ASSERTION = re.compile(r"assert|assert[A-Z]\w*|fail|raises|_?assert_\w+")
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


def _names_read(test: TestFunction) -> set[str]:
    return {
        node.id
        for node in ast.walk(test)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _nodes_that_run(test: TestFunction) -> Iterator[ast.AST]:
    """The nodes of a test, without the functions and classes it defines and never uses."""

    read = _names_read(test)
    pending = list(ast.iter_child_nodes(test))
    while pending:
        node = pending.pop()
        if isinstance(node, Definition) and not node.decorator_list and node.name not in read:
            continue
        if isinstance(node, ast.ClassDef):
            # The methods of a class in use are called through its instances.
            yield from ast.walk(node)
        else:
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
