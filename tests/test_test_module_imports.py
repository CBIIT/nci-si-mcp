"""Ratchet imports ending in a segment starting `test_` across tracked root-test Python files."""

import ast
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CROSS_IMPORT_TARGETS = 184


def is_test_module(name: str) -> bool:
    return name.rsplit(".", maxsplit=1)[-1].startswith("test_")


def import_targets(node: ast.AST) -> int:
    if isinstance(node, ast.Import):
        return sum(is_test_module(alias.name) for alias in node.names)
    return import_from_targets(node) if isinstance(node, ast.ImportFrom) else 0


def import_from_targets(node: ast.ImportFrom) -> int:
    if node.module is not None and is_test_module(node.module):
        return 1
    if node.level > 0:
        return sum(is_test_module(alias.name) for alias in node.names)
    if node.module == "tests":
        return sum(is_test_module(alias.name) for alias in node.names)
    return 0


def cross_import_targets(source: str) -> int:
    return sum(import_targets(node) for node in ast.walk(ast.parse(source)))


def tracked_test_sources() -> list[Path]:
    result = subprocess.run(
        ["/usr/bin/git", "ls-files", "-z", "tests"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / name for name in result.stdout.decode().split("\0") if name.endswith(".py")]


class TestModuleImportRatchet(unittest.TestCase):
    def test_scanner_counts_direct_qualified_multiline_and_relative_targets(self):
        source = """
import test_one, package.test_two
from test_three import (
    helper,
)
from package.test_four import helper
from . import test_five, helper
from tests import test_six, helper
from helper import test_value
"""

        self.assertEqual(cross_import_targets(source), 6)

    def test_tracked_test_modules_do_not_gain_cross_import_targets(self):
        found = sum(
            cross_import_targets(path.read_text(encoding="utf-8"))
            for path in tracked_test_sources()
        )

        self.assertEqual(
            found,
            CROSS_IMPORT_TARGETS,
            "Move a touched shared helper to tests/fakes.py; lower the ratchet after a reduction",
        )
