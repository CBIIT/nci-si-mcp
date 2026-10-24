"""Coverage floors, warning aims and contributor statements agree exactly."""

# ruff: noqa: E501 - exact workflow snapshots preserve literal commands and messages.

import tomllib
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def floor(name):
    return tomllib.loads((ROOT / name).read_text())["tool"]["coverage"]["report"]["fail_under"]


class CoveragePolicyTest(unittest.TestCase):
    def test_manual_ci_is_nonpublishing_and_never_cancelled(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        self.assertEqual(
            workflow[True],
            {"push": {"branches": ["main"]}, "pull_request": None, "workflow_dispatch": None},
        )
        self.assertEqual(
            workflow["concurrency"],
            {
                "group": "ci-${{ github.event_name == 'pull_request' && github.ref || github.sha }}",
                "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
            },
        )
        self.assertEqual(
            workflow["jobs"]["coverage-badges"]["if"],
            "github.event_name == 'push' && github.ref == 'refs/heads/main'",
        )
        release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
        self.assertEqual(
            release["jobs"]["release"]["if"],
            "github.event_name == 'workflow_dispatch' || "
            "(github.event.workflow_run.conclusion == 'success' &&\n "
            "github.event.workflow_run.event == 'push' &&\n "
            "github.event.workflow_run.head_branch == 'main')",
        )

    def test_both_suites_enforce_the_exact_floor(self):
        self.assertEqual([floor("pyproject.toml"), floor("acceptance/pyproject.toml")], [95, 95])

    def test_ci_reports_the_exact_warning_aim_without_changing_measurement(self):
        jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
        server = next(
            step
            for step in jobs["test"]["steps"]
            if step.get("name") == "Report coverage against the aim"
        )
        harness = next(
            step
            for step in jobs["coverage"]["steps"]
            if step.get("name") == "Combine the shards and enforce the minimum"
        )
        self.assertEqual(
            server,
            {
                "name": "Report coverage against the aim",
                "run": 'total="$(pdm run coverage report --format=total)"\n'
                'echo "Coverage ${total}% (the aim is above 97%)" >> "$GITHUB_STEP_SUMMARY"\n'
                "if awk -v total=\"$total\" 'BEGIN { exit !(total <= 97) }'; then\n"
                '  echo "::warning title=Coverage is not above 97%::${total}%. Cover the untested behaviour; do not pad."\nfi\n',
            },
        )
        self.assertEqual(
            harness,
            {
                "name": "Combine the shards and enforce the minimum",
                "run": "pdm run coverage combine --rcfile=acceptance/pyproject.toml --data-file=.coverage-selftest selftest-coverage/*/.coverage\n"
                "pdm run coverage report --rcfile=acceptance/pyproject.toml --data-file=.coverage-selftest\n"
                'total="$(pdm run coverage report --rcfile=acceptance/pyproject.toml --data-file=.coverage-selftest --format=total)"\n'
                'echo "Self-test coverage ${total}% (the aim is above 97%)" >> "$GITHUB_STEP_SUMMARY"\n'
                "if awk -v total=\"$total\" 'BEGIN { exit !(total <= 97) }'; then\n"
                '  echo "::warning title=Self-test coverage is not above 97%::${total}%. Cover the untested behaviour; do not pad."\nfi\n',
            },
        )
        self.assertEqual(
            [step["run"] for step in jobs["selftest"]["steps"] if "run" in step],
            ["pdm run acceptance-selftest --cov-fail-under=0"],
        )

    def test_documented_thresholds_match_the_gate(self):
        minimum = floor("pyproject.toml")
        expected = {
            "AGENTS.md": f"**Coverage: the checked minimum is {minimum}%, the aim is above 97%.**",
            "CONTRIBUTING.md": f"**Coverage: {minimum}% is the checked minimum, above 97% is the aim.**",
            "docs/evidence-contract.md": f"The existing {minimum}% floor and above-97% aim apply",
        }
        for name, statement in expected.items():
            with self.subTest(name=name):
                self.assertIn(statement, (ROOT / name).read_text())
