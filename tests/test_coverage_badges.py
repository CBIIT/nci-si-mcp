"""Coverage badges report measured gaps and publish only isolated, current-main data."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from scripts import coverage_badges

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
JOB = WORKFLOW["jobs"]["coverage-badges"]
PUBLISH = next(
    step["run"] for step in JOB["steps"] if step.get("name") == "Publish generated badge data"
)


def report(lines=100, branches=100, total=100):
    return {
        "meta": {"branch_coverage": True},
        "totals": {
            "num_statements": total,
            "covered_lines": lines,
            "num_branches": total,
            "covered_branches": branches,
        },
    }


class CoverageBadgeTest(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)

    def test_badge_counts_branches_and_preserves_tiny_gaps(self):
        cases = [
            (report(100, 100), "100.00%", "brightgreen"),
            (report(100, 90), "95.00%", "yellow"),
            (report(100, 70), "85.00%", "red"),
            (report(100_000, 99_999, 100_000), "99.99%", "brightgreen"),
        ]
        for source, message, color in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    coverage_badges.badge(source, "server coverage"),
                    {
                        "schemaVersion": 1,
                        "label": "server coverage",
                        "message": message,
                        "color": color,
                    },
                )

    def test_incomplete_or_inconsistent_reports_never_become_badges(self):
        cases = [report(-1), report(101), report(True), report(1.5), report(0, 0, 0)]
        cases += [{"meta": {"branch_coverage": False}, "totals": report()["totals"]}]
        cases += [report() | {"totals": None}, report() | {"meta": None}, {"meta": {}}]
        for source in cases:
            with self.subTest(source=source), self.assertRaises((ValueError, KeyError)):
                coverage_badges.badge(source, "coverage")

    def test_combined_coverage_weights_the_actual_number_of_lines_and_branches(self):
        source = report(90, 1)
        source["totals"]["num_branches"] = 10

        actual = coverage_badges.badge(source, "server coverage")

        self.assertEqual(actual["message"], "82.73%")
        self.assertEqual(actual["color"], "red")

    def test_ci_command_writes_both_badges_and_the_measured_run_identity(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            root = Path(directory)
            output = root / "output"
            for name, measurement in (("server", report()), ("harness", report(100, 98))):
                (root / f"{name}.json").write_text(json.dumps(measurement))
            environment = os.environ | {
                "GITHUB_REPOSITORY": "test-org/test-repo",
                "GITHUB_SERVER_URL": "https://github.example.invalid",
                "GITHUB_RUN_ID": "12345",
                "GITHUB_SHA": "measured-commit",
            }
            result = subprocess.run(  # noqa: S603 - repository script, local reports, no network
                [sys.executable, str(ROOT / "scripts/coverage_badges.py"), str(root), str(output)],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            for name, measurement in (("server", report()), ("harness", report(100, 98))):
                self.assertEqual(
                    json.loads((output / f"{name}.json").read_text()),
                    coverage_badges.badge(measurement, f"{name} coverage"),
                )
            provenance = json.loads((output / "provenance.json").read_text())
            self.assertEqual(provenance["commit"], "measured-commit")
            self.assertEqual(
                provenance["run"],
                "https://github.example.invalid/test-org/test-repo/actions/runs/12345",
            )

    def test_render_links_both_measurements_to_the_run_and_requires_both_reports(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            root = Path(directory)
            destination = root / "output"
            (root / "server.json").write_text(json.dumps(report()))
            with self.assertRaises(FileNotFoundError):
                coverage_badges.render(root, destination, "commit", "run")
            self.assertFalse(destination.exists())
            (root / "harness.json").write_text(json.dumps(report(100, 98)))
            coverage_badges.render(root, destination, "commit", "run")
            provenance = json.loads((destination / "provenance.json").read_text())
            self.assertEqual(
                provenance,
                {
                    "commit": "commit",
                    "run": "run",
                    "totals": {
                        "server": report()["totals"],
                        "harness": report(100, 98)["totals"],
                    },
                },
            )
            self.assertEqual(
                json.loads((destination / "harness.json").read_text())["message"], "99.00%"
            )

    def test_publication_requires_main_push_and_every_ci_gate(self):
        self.assertEqual(
            JOB["if"], "github.event_name == 'push' && github.ref == 'refs/heads/main'"
        )
        self.assertEqual(set(JOB["needs"]), set(WORKFLOW["jobs"]) - {"selftest", "coverage-badges"})
        self.assertEqual(WORKFLOW["jobs"]["coverage"]["needs"], "selftest")
        self.assertFalse(JOB["concurrency"]["cancel-in-progress"])
        self.assertEqual(WORKFLOW["permissions"], {"contents": "read"})


class CoveragePublicationTest(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote = self.root / "origin" / "repo.git"
        self.remote.parent.mkdir()
        self.git("init", "--bare", "--initial-branch=main", str(self.remote))
        self.work = self.root / "work"
        self.work.mkdir()
        self.git("init", "--initial-branch=main", str(self.work))
        self.git("-C", str(self.work), "config", "user.name", "Test role")
        self.git("-C", str(self.work), "config", "user.email", "test@example.invalid")
        (self.work / "README.md").write_text("main must stay unchanged\n")
        self.git("-C", str(self.work), "add", "README.md")
        self.git("-C", str(self.work), "commit", "-m", "test: initial main")
        self.sha = self.git("-C", str(self.work), "rev-parse", "HEAD")
        self.git("-C", str(self.work), "push", str(self.remote), "main")
        binary = self.root / "bin"
        binary.mkdir()
        gh = binary / "gh"
        gh.write_text('#!/bin/sh\nif [ "$1" = api ]; then printf "%s\\n" "$BADGE_MAIN_SHA"; fi\n')
        gh.chmod(0o700)
        self.environment = os.environ | {
            "PATH": f"{binary}:{os.environ['PATH']}",
            "GITHUB_SERVER_URL": str(self.remote.parent),
            "GITHUB_REPOSITORY": "repo",
            "GITHUB_SHA": self.sha,
            "BADGE_MAIN_SHA": self.sha,
        }
        self.badges = self.work / "tmp" / "coverage-badges"
        self.badges.mkdir(parents=True)
        for name in ("server", "harness", "provenance"):
            (self.badges / f"{name}.json").write_text('{"message":"99.99%"}\n')

    def git(self, *arguments):
        return subprocess.check_output(  # noqa: S603 - local temporary repositories, fixed executable
            [shutil.which("git"), *arguments],
            text=True,
            stderr=subprocess.PIPE,
        ).strip()

    def publish(self, **environment):
        runner = tempfile.mkdtemp(dir=self.root)
        return subprocess.run(  # noqa: S603 - repository workflow, local bare remote, no credentials
            [shutil.which("bash"), "-c", PUBLISH],
            cwd=self.work,
            env=self.environment | {"RUNNER_TEMP": runner} | environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )

    def test_first_publication_and_refresh_preserve_main_and_badge_history(self):
        first = self.publish()
        self.assertEqual(first.returncode, 0, first.stderr)
        old = self.git("-C", str(self.remote), "rev-parse", "coverage-badges")
        (self.badges / "server.json").write_text('{"message":"98.00%"}\n')
        second = self.publish()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.git("-C", str(self.remote), "rev-parse", "coverage-badges^"), old)
        self.assertEqual(
            self.git("-C", str(self.remote), "show", "coverage-badges:server.json"),
            '{"message":"98.00%"}',
        )
        self.assertEqual(self.git("-C", str(self.remote), "rev-parse", "main"), self.sha)
        unchanged = self.publish()
        self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
        self.assertEqual(
            self.git("-C", str(self.remote), "rev-list", "--count", "coverage-badges"), "2"
        )

    def test_superseded_commit_leaves_published_badges_untouched(self):
        first = self.publish()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.git("-C", str(self.remote), "rev-parse", "coverage-badges")
        (self.badges / "server.json").write_text('{"message":"0.00%"}\n')
        stale = self.publish(BADGE_MAIN_SHA="new-main")
        self.assertEqual(stale.returncode, 0, stale.stderr)
        self.assertEqual(self.git("-C", str(self.remote), "rev-parse", "coverage-badges"), before)

    def test_missing_measurement_fails_without_creating_a_badge_branch(self):
        (self.badges / "harness.json").unlink()
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.git("-C", str(self.remote), "branch", "--list", "coverage-badges"), ""
        )
