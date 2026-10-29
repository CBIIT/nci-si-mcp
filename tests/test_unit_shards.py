"""Unit CI partitions preserve collected nodes, child runs and coverage ownership."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.validation.mutation_run import coverage_args, coverage_map

ROOT = Path(__file__).resolve().parents[1]


def project(root):
    (root / "tests").mkdir()
    (root / "src").mkdir()
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    shutil.copyfile(ROOT / "tests/conftest.py", root / "tests/conftest.py")


def run(root, *arguments, env=None):
    clean = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE", "PYTEST", "MUTATION_"))
    }
    clean["PYTHONPATH"] = os.pathsep.join((str(root / "src"), str(ROOT)))
    return subprocess.run(  # noqa: S603 - fixed interpreter, owned project and pytest arguments
        [sys.executable, "-m", "pytest", "-p", "no:rerunfailures", *arguments],
        cwd=root,
        env=clean | (env or {}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def contexts(mapping):
    return {
        (file, line): set(values)
        for file, lines in mapping.items()
        for line, values in lines.items()
    }


class UnitShardsTest(unittest.TestCase):
    def test_collection_partitions_every_node_once_and_nested_runs_keep_all_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            (root / "tests/test_cases.py").write_text(
                "import subprocess, sys\nimport pytest\n"
                "@pytest.mark.parametrize('value', range(7))\n"
                "def test_case(value):\n    assert value >= 0\n"
                "def test_nested():\n"
                "    result = subprocess.run([sys.executable, '-m', 'pytest', "
                "'--collect-only', '-q', 'child'], capture_output=True, text=True)\n"
                "    assert result.returncode == 0, result.stdout + result.stderr\n"
                "    assert '5 tests collected' in result.stdout, result.stdout\n"
            )
            (root / "child").mkdir()
            (root / "child/test_child.py").write_text(
                "import pytest\n@pytest.mark.parametrize('value', range(5))\n"
                "def test_child(value):\n    assert value >= 0\n"
            )
            expected = [
                *(f"tests/test_cases.py::test_case[{i}]" for i in range(7)),
                "tests/test_cases.py::test_nested",
            ]
            actual = []
            for index in (1, 2, 3):
                spec = f"{index}/3"
                collected = run(root, "--collect-only", "-q", "--unit-shard", spec)
                self.assertEqual(collected.returncode, 0, collected.stdout + collected.stderr)
                nodes = [line for line in collected.stdout.splitlines() if "::" in line]
                self.assertEqual(nodes, sorted(expected)[index - 1 :: 3])
                actual.extend(nodes)
                executed = run(root, "-q", "--unit-shard", spec, "-n", "2")
                self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
            self.assertEqual(sorted(actual), sorted(expected))
            serial = run(root, "--collect-only", "-q")
            self.assertEqual(serial.returncode, 0, serial.stdout + serial.stderr)
            self.assertEqual(
                [line for line in serial.stdout.splitlines() if "::" in line], expected
            )

    def test_invalid_shard_is_a_usage_error_not_an_empty_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            (root / "tests/test_case.py").write_text("def test_case():\n    assert True\n")
            for spec in ("0/3", "4/3", "1/0", "1", "a/3", "1/2/3"):
                with self.subTest(spec=spec):
                    result = run(root, "--unit-shard", spec)
                    self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
                    self.assertIn("shard must be K/N with 1 <= K <= N", result.stderr)

    def test_two_worker_shards_preserve_serial_child_and_phase_contexts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project(root)
            (root / "src/subject.py").write_text(
                "def setup():\n    return 'setup'\n"
                "def call():\n    return 'call'\n"
                "def teardown():\n    return 'teardown'\n"
            )
            (root / "src/child.py").write_text(
                "import sys, subject\nprint(getattr(subject, sys.argv[1])())\n"
            )
            (root / "tests/test_phases.py").write_text(
                "import subprocess, sys, pytest\n"
                "def launch(phase):\n"
                "    result = subprocess.run([sys.executable, '-m', 'child', phase], "
                "capture_output=True, text=True)\n"
                "    assert result.returncode == 0, result.stderr\n"
                "    assert result.stdout.strip() == phase\n"
                "@pytest.fixture\ndef phases():\n"
                "    launch('setup')\n    yield\n    launch('teardown')\n"
                "def test_a(phases):\n    launch('call')\n"
                "def test_b(phases):\n    launch('call')\n"
                "def test_c(phases):\n    launch('call')\n"
            )
            serial = self.measure(root, "serial", "-n", "0")
            combined = {}
            for index in (1, 2, 3):
                measured = self.measure(
                    root, f"shard-{index}", "--unit-shard", f"{index}/3", "-n", "2"
                )
                for key, values in measured.items():
                    combined.setdefault(key, set()).update(values)
            self.assertEqual(combined, serial)
            for line, phase in ((2, "setup"), (4, "run"), (6, "teardown")):
                self.assertEqual(
                    combined["src/subject.py", line],
                    {f"tests/test_phases.py::test_{name}|{phase}" for name in "abc"},
                )

    def measure(self, root, name, *arguments):
        work = root / name
        work.mkdir()
        env = {"COVERAGE_FILE": str(work / ".coverage")}
        args = coverage_args(root, work, True, env)
        result = run(root, "-p", "scripts.validation.mutation_probe", *args, *arguments, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        mapping = contexts(coverage_map(work / ".coverage", root))
        # Compare the production subject, not collection of the test code or generated configs.
        return {key: values for key, values in mapping.items() if key[0].startswith("src/")}
