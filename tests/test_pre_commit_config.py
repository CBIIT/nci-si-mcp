"""Local hooks and CI run the exact gates assigned to their boundaries."""

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from pre_commit.clientlib import load_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".pre-commit-config.yaml"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def configuration():
    return yaml.safe_load(CONFIG.read_text())


def hooks():
    return [hook for repository in configuration()["repos"] for hook in repository["hooks"]]


class RuffHooksTest(unittest.TestCase):
    def test_both_ruff_hooks_run_without_the_cache(self):
        entries = {
            hook["id"]: hook["entry"]
            for hook in hooks()
            if hook["id"] in {"ruff-check", "ruff-format"}
        }
        self.assertEqual(
            entries,
            {
                "ruff-format": "pdm run ruff format --no-cache --force-exclude",
                "ruff-check": (
                    "pdm run ruff check --no-cache --fix --exit-non-zero-on-fix --force-exclude"
                ),
            },
        )


class PrePushTestHookTest(unittest.TestCase):
    def test_installation_includes_exactly_commit_and_push_hooks(self):
        self.assertEqual(configuration()["default_install_hook_types"], ["pre-commit", "pre-push"])

    def test_push_runs_the_whole_suite_with_exact_configuration(self):
        (hook,) = [hook for hook in hooks() if hook["id"] == "full-test-suite"]

        self.assertEqual(
            hook,
            {
                "id": "full-test-suite",
                "name": "whole test suite before push",
                "entry": "pdm run test",
                "language": "system",
                "stages": ["pre-push"],
                "pass_filenames": False,
                "always_run": True,
            },
        )

    def test_only_the_full_suite_hook_runs_before_push(self):
        config = load_config(CONFIG)
        effective = {
            hook["id"]: hook.get("stages") or config["default_stages"]
            for repository in config["repos"]
            for hook in repository["hooks"]
        }

        self.assertEqual(effective.pop("full-test-suite"), ["pre-push"])
        self.assertEqual(set(map(tuple, effective.values())), {("pre-commit",)})

    def test_real_install_creates_exactly_commit_and_push_hooks(self):
        with TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(
                ["git", "init", "--quiet"],  # noqa: S607 - Git is the developer boundary.
                cwd=repository,
                check=True,
            )
            subprocess.run(  # noqa: S603 - all arguments are fixed or repository-owned paths.
                [sys.executable, "-m", "pre_commit", "install", "--config", str(CONFIG)],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            )
            installed = sorted(
                path.name
                for path in (repository / ".git" / "hooks").iterdir()
                if not path.name.endswith(".sample")
            )

        self.assertEqual(installed, ["pre-commit", "pre-push"])

    def test_ci_runs_only_the_default_commit_stage(self):
        workflow = yaml.safe_load(CI.read_text())
        commands = [step["run"] for step in workflow["jobs"]["quality"]["steps"] if "run" in step]

        self.assertEqual(
            commands,
            ["pdm run pre-commit run --all-files --show-diff-on-failure"],
        )
