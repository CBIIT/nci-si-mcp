"""The mutation command measures assertion sensitivity without editing its caller."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MutationCommandTest(unittest.TestCase):
    def test_an_unkilled_mutant_fails_with_source_and_fixture_confirmation(self):
        # Given an executed return whose value neither domain checks, when the gate runs,
        # then it reports the survivor after trying the fixture owner, and fails closed.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "src/example.py": "def answer():\n    return True\n",
                "tests/test_example.py": (
                    "from example import answer\ndef test_call():\n"
                    "    assert answer() is not Ellipsis\n"
                ),
                "acceptance/selftests/test_harness.py": (
                    "def test_harness():\n    assert 2 + 2 == 4\n"
                ),
                "acceptance/tests/test_contract.py": (
                    "def test_contract():\n    assert 3 + 3 == 6\n"
                ),
                "acceptance/conftest.py": (
                    "def pytest_addoption(parser):\n    parser.addoption('--report')\n"
                ),
                "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
            }
            for name, text in files.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            before = (root / "src/example.py").read_bytes()
            result = subprocess.run(  # noqa: S603 - fixed project command, owned fixture path
                ["pdm", "run", "mutation-gate", "--root", str(root), "--paths", "src/example.py"],  # noqa: S607
                cwd=ROOT,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if not key.startswith("COVERAGE")
                },
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            report = [json.loads(line) for line in result.stdout.splitlines()]
            survivors = [row for row in report if row.get("outcome") == "SURVIVED"]
            self.assertTrue(survivors, result.stdout)
            self.assertEqual(survivors[0]["file"], "src/example.py")
            self.assertEqual(survivors[0]["line"], 2)
            self.assertEqual(survivors[0]["before"], "True")
            self.assertEqual(survivors[0]["after"], "False")
            self.assertEqual(survivors[0]["confirmation"], "fixture")
            self.assertEqual((root / "src/example.py").read_bytes(), before)
