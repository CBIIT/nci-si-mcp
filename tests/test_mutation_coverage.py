"""Shared CI baselines retain local routes and reject unusable inputs at the CLI."""

import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from coverage import CoverageData
from scripts.validation.mutation_coverage import SHARDS, export, identity, load
from scripts.validation.mutation_run import selection

from test_mutation_gate import ROOT, cli, project


def repository(root):
    project(root, "def answer():\n    return True\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)  # noqa: S607
    subprocess.run(["git", "add", "."], cwd=root, check=True)  # noqa: S607
    subprocess.run(
        [  # noqa: S607 - fixed fixture command; no hooks exist in this owned repository
            "git",
            "-c",
            "user.name=Test role",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "test: coverage tree",
        ],
        cwd=root,
        check=True,
    )


def fragments(root, directory):
    directory.mkdir()
    for domain, count in SHARDS.items():
        node = {
            "unit": "tests/test_example.py",
            "selftest": "selftests/test_harness.py",
            "fixture": "tests/test_contract.py",
        }[domain]
        for index in range(1, count + 1):
            data_file = root / f".coverage-{domain}-{index}"
            data = CoverageData(basename=str(data_file))
            data.set_context(f"{node}::test_value|run")
            data.add_lines({str(root / "src/example.py"): {2}})
            data.write()
            export(
                root, data_file, domain, f"{index}/{count}", directory / f"{domain}-{index}.json"
            )


class SharedCoverageTest(unittest.TestCase):
    def test_fragment_membership_and_corruption_fail_before_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repository(root)
            fragments(root, root / "coverage")
            path = root / "coverage/unit-1.json"
            original = path.read_text()
            for content in ("{", "[]", original.replace('"version": 1', '"version": true')):
                with self.subTest(content=content[:20]):
                    path.write_text(content)
                    result = cli(
                        root,
                        "--base",
                        "HEAD",
                        "--head",
                        "HEAD",
                        "--coverage-dir",
                        str(root / "coverage"),
                    )
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(json.loads(result.stdout)["outcome"], "INFRA")
            path.write_text(original)
            path.rename(root / "missing.json")
            with self.assertRaisesRegex(ValueError, "Missing coverage shards"):
                load(root, root / "coverage")
            path.write_text(original)
            (root / "coverage/duplicate.json").write_text(original)
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load(root, root / "coverage")

    def test_complete_domain_fragments_merge_routes_without_recomputing_a_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repository(root)
            fragments(root, root / "coverage")
            mapping = load(root, root / "coverage")
            self.assertEqual(
                selection(mapping, "src/example.py", 2, 2),
                {
                    "unit": ["tests/test_example.py::test_value"],
                    "selftest": ["selftests/test_harness.py::test_value"],
                    "fixture": ["tests/test_contract.py::test_value"],
                },
            )
            # An empty catalogue still validates the explicit import, then exits without baselines.
            result = cli(
                root, "--base", "HEAD", "--head", "HEAD", "--coverage-dir", str(root / "coverage")
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["outcome"], "EMPTY")

    def test_invalid_fragment_contracts_are_infrastructure_not_silent_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repository(root)
            fragments(root, root / "coverage")
            path = root / "coverage/unit-1.json"
            original = json.loads(path.read_text())
            invalid = [
                {"version": 2},
                {"sha": "other"},
                {"inputs": "other"},
                {"domain": []},
                {"shard": "8/4"},
                {"mapping": {}},
                {"mapping": {"../outside.py": {"2": ["tests/t.py::test_x|run"]}}},
                {"mapping": {"src/example.py": {"0": ["tests/t.py::test_x|run"]}}},
                {"mapping": {"src/example.py": {"2": [3]}}},
                {"mapping": {"src/example.py": {"2": ["tests/t.py::test_x|bad"]}}},
                {"mapping": {"src/example.py": {"2": ["selftests/t.py::test_x|run"]}}},
                {"mapping": {"src/example.py": {"2": [""]}}},
            ]
            for change in invalid:
                with self.subTest(change=change):
                    path.write_text(json.dumps(original | deepcopy(change)))
                    result = cli(
                        root,
                        "--base",
                        "HEAD",
                        "--head",
                        "HEAD",
                        "--coverage-dir",
                        str(root / "coverage"),
                    )
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(json.loads(result.stdout)["outcome"], "INFRA")
            path.write_text(json.dumps(original))
            before = identity(root)
            (root / "src/example.py").write_text("def answer():\n    return False\n")
            self.assertNotEqual(identity(root), before)
            result = cli(
                root, "--base", "HEAD", "--head", "HEAD", "--coverage-dir", str(root / "coverage")
            )
            self.assertEqual(json.loads(result.stdout)["outcome"], "INFRA")

    def test_explicit_missing_coverage_is_infrastructure_even_with_no_changed_sites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root, "def answer():\n    return True\n")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)  # noqa: S607
            result = cli(root, "--paths", "README.md", "--coverage-dir", str(root / "absent"))

            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            rows = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([row["outcome"] for row in rows], ["INFRA"])
            self.assertIn("coverage", rows[0]["message"].lower())
            self.assertNotIn("Baseline unit", rows[0]["message"])

    def test_coverage_command_missing_database_cannot_export_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = subprocess.run(  # noqa: S603 - fixed CLI and owned test paths
                [  # noqa: S607 - project PDM environment
                    "pdm",
                    "run",
                    "python",
                    "-m",
                    "scripts.validation.mutation_coverage",
                    "export",
                    "unit",
                    "--root",
                    str(root),
                    "--data",
                    str(root / "absent"),
                    "--shard",
                    "1/4",
                    "--output",
                    str(root / "piece.json"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["outcome"], "INFRA")
            self.assertFalse((root / "piece.json").exists())
