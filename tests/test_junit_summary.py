"""Summaries reflect real pytest JUnit outcomes without copying diagnostic payloads."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/junit_summary.py"
SAMPLE = """
import unittest
import pytest

def test_pass():
    assert True

def test_failure():
    assert False, "PRIVATE-BODY"

@pytest.mark.skip(reason="PRIVATE-SKIP")
def test_skip():
    assert False

@pytest.fixture
def broken():
    raise RuntimeError("PRIVATE-ERROR")

def test_error(broken):
    assert broken

class Subtests(unittest.TestCase):
    def test_cases(self):
        with self.subTest(value="case"):
            self.assertEqual(1, 2, "PRIVATE-SUBTEST")
"""


class JunitSummaryTest(unittest.TestCase):
    def render(self, path):
        return subprocess.run(  # noqa: S603 - fixed repository CLI and owned report.
            [sys.executable, str(SCRIPT), str(path), "Unit tests"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def test_real_pytest_results_include_failed_subtests_without_diagnostic_bodies(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath("pytest.ini").write_text("[pytest]\n")
            root.joinpath("test_sample.py").write_text(SAMPLE)
            produced = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "test_sample.py",
                    "--junitxml=report.xml",
                    "-p",
                    "no:cacheprovider",
                    "-q",
                ],
                cwd=root,
                env=os.environ | {"PYTEST_ADDOPTS": ""},
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(produced.returncode, 1, produced.stdout + produced.stderr)
            result = self.render(root / "report.xml")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "## Unit tests\n\n"
            "| Total | Passed | Failed | Errors | Skipped |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 5 | 1 | 2 | 1 | 1 |\n\n"
            "### Failed tests\n\n"
            "- failure: test_sample::test_failure\n"
            "- error: test_sample::test_error\n"
            "- failure: test_sample.Subtests::test_cases\n",
        )
        self.assertNotIn("PRIVATE-", result.stdout + result.stderr)

    def test_missing_malformed_and_empty_reports_fail_visibly(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.xml"
            for value in (None, "<broken", "<testsuites/>"):
                with self.subTest(value=value):
                    if value is not None:
                        path.write_text(value)
                    result = self.render(path)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(
                        result.stderr,
                        "JUnit report unavailable or invalid; "
                        "inspect the test step and XML artifact.\n",
                    )
