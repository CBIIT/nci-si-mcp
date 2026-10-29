"""Shared CI baselines retain local routes and reject unusable inputs at the CLI."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_mutation_gate import ROOT, cli, project


class SharedCoverageTest(unittest.TestCase):
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
