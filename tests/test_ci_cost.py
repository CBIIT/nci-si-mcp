"""Nothing the Local run adds changes what ci.yml starts, or where, on a pull request."""

import unittest
from pathlib import Path

import yaml

CI = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))


class CiCostTest(unittest.TestCase):
    def test_ci_starts_on_the_same_events(self):
        self.assertEqual(CI[True], {"push": {"branches": ["main"]}, "pull_request": None})

    def test_every_ci_job_keeps_its_platform_dependencies_and_condition(self):
        companions = (
            "${{ !cancelled() && (github.event_name != 'pull_request' || "
            "needs.changes.outputs.companions == 'true') }}"
        )
        image = "github.event_name != 'pull_request' || needs.changes.outputs.image == 'true'"
        badges = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
        expected = {
            "changes": (None, None),
            "companions": (["changes", "documentation"], companions),
            "documentation": (None, None),
            "image": (["changes"], image),
            "quality": (None, None),
            "test": (None, None),
            "acceptance": (None, None),
            "acceptance-http": (None, None),
            "selftest": (None, None),
            "coverage": ("selftest", None),
            "coverage-badges-render": (["test", "coverage"], None),
            "coverage-badges": (
                [
                    "changes",
                    "quality",
                    "test",
                    "coverage",
                    "acceptance",
                    "acceptance-http",
                    "image",
                    "documentation",
                    "companions",
                    "coverage-badges-render",
                ],
                badges,
            ),
        }
        jobs = CI["jobs"]
        self.assertEqual(
            {name: j.get("runs-on") for name, j in jobs.items()},
            dict.fromkeys(expected, "ubuntu-latest"),
        )
        self.assertEqual(
            {name: (j.get("needs"), j.get("if")) for name, j in jobs.items()}, expected
        )
