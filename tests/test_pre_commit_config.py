"""The ruff hooks run without ruff's cache, so a local pass agrees with CI."""

import unittest
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / ".pre-commit-config.yaml"


class RuffHooksTest(unittest.TestCase):
    def test_both_ruff_hooks_run_without_the_cache(self):
        repositories = yaml.safe_load(CONFIG.read_text())["repos"]
        entries = {
            hook["id"]: hook["entry"]
            for repository in repositories
            for hook in repository["hooks"]
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
