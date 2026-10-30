"""Workflow linting retains syntax and shell sensitivity beside narrow $/ ignores."""

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from test_pre_commit_config import hooks

ROOT = Path(__file__).resolve().parents[1]
IGNORES = [
    r'^specifying action "\$/[^"\n]+" in invalid format because ref is missing\. '
    r'available formats are "\{owner\}/\{repo\}@\{ref\}" or '
    r'"\{owner\}/\{repo\}/\{path\}@\{ref\}"$',
    r'^reusable workflow call "\$/[^"\n]+" at "uses" is not following the format '
    r'"owner/repo/path/to/workflow\.yml@ref" nor "\./path/to/workflow\.yml"\. '
    r"see https://docs\.github\.com/en/actions/learn-github-actions/"
    r"reusing-workflows for more details$",
]


class ActionlintContractTest(unittest.TestCase):
    def test_hook_scans_all_workflows_from_the_locked_pdm_environment(self):
        actual = [hook for hook in hooks() if hook["id"] == "actionlint"]
        self.assertEqual(
            actual,
            [
                {
                    "id": "actionlint",
                    "name": "actionlint",
                    "entry": "pdm run actionlint",
                    "language": "system",
                    "files": r"^\.github/(workflows/.*\.ya?ml|actionlint\.yaml)$",
                    "pass_filenames": False,
                    "stages": ["pre-commit"],
                }
            ],
        )

    def test_only_exact_self_repository_diagnostics_are_ignored(self):
        path = ROOT / ".github/actionlint.yaml"
        self.assertTrue(path.is_file(), "The narrow actionlint configuration must exist")
        self.assertEqual(
            yaml.safe_load(path.read_text()),
            {"paths": {".github/workflows/**/*.{yml,yaml}": {"ignore": IGNORES}}},
        )

    def test_every_self_repository_use_names_an_existing_action_or_workflow(self):
        missing = []
        for path in (ROOT / ".github").rglob("*.yml"):
            value = yaml.safe_load(path.read_text())
            candidates = list(value.get("jobs", {}).values())
            candidates.extend(value.get("runs", {}).get("steps", []))
            for job in value.get("jobs", {}).values():
                candidates.extend(job.get("steps", []))
            for candidate in candidates:
                target = candidate.get("uses", "")
                if target.startswith("$/"):
                    location = ROOT / target[2:]
                    if not (location.is_file() or (location / "action.yml").is_file()):
                        missing.append((str(path.relative_to(ROOT)), target))
        self.assertEqual(missing, [])


class ActionlintExecutionTest(unittest.TestCase):
    def lint(self, body: str):
        self.assertIsNotNone(shutil.which("actionlint"), "Install actionlint from the PDM lock")
        self.assertIsNotNone(shutil.which("shellcheck"), "Install shellcheck from the PDM lock")
        for tool in ("actionlint", "shellcheck"):
            self.assertEqual(
                Path(shutil.which(tool) or "").parent,
                Path(sys.executable).parent,
                f"{tool} must come from the PDM environment",
            )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.yml"
            path.write_text(body)
            return subprocess.run(  # noqa: S603 - fixed linter with a temporary test workflow
                [
                    shutil.which("actionlint") or "actionlint",
                    "-config-file",
                    str(ROOT / ".github/actionlint.yaml"),
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

    def test_valid_workflow_passes_but_missing_dependency_fails(self):
        base = "name: Check\non: push\njobs:\n  check:\n    runs-on: ubuntu-latest\n"
        valid = self.lint(base + "    steps:\n      - run: echo ok\n")
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        invalid = self.lint(base + "    needs: missing-job\n    steps:\n      - run: echo ok\n")
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("missing-job", invalid.stdout + invalid.stderr)

    def test_shellcheck_finding_is_not_silently_disabled(self):
        result = self.lint(
            "name: Check\non: push\njobs:\n  check:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: echo $1\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SC2086", result.stdout + result.stderr)
