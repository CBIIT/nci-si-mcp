"""The write-token job accepts rendered data, never repository execution."""

import json
import unittest
from pathlib import Path

import yaml

WORKFLOW = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())


class BadgeTokenBoundaryTest(unittest.TestCase):
    def test_only_the_main_publisher_has_write_authority(self):
        self.assertEqual(WORKFLOW["permissions"], {"contents": "read"})
        writers = {
            name
            for name, job in WORKFLOW["jobs"].items()
            if "write" in job.get("permissions", {}).values()
        }
        self.assertEqual(writers, {"coverage-badges"})
        writer = WORKFLOW["jobs"]["coverage-badges"]
        self.assertEqual(
            writer.get("if"), "github.event_name == 'push' && github.ref == 'refs/heads/main'"
        )
        self.assertEqual(
            set(writer["needs"]),
            {
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
            },
        )
        self.assertEqual(
            set(writer["needs"]), set(WORKFLOW["jobs"]) - {"selftest", "coverage-badges"}
        )
        self.assertEqual(WORKFLOW["jobs"]["coverage"]["needs"], "selftest")
        self.assertEqual(
            writer.get("concurrency"),
            {"group": "coverage-badges-main", "cancel-in-progress": False},
        )
        self.assertEqual(writer["steps"][1]["env"], {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"})

    def test_no_checkout_retains_credentials_for_repository_code(self):
        for job in WORKFLOW["jobs"].values():
            for step in job["steps"]:
                if step.get("uses", "").startswith("actions/checkout@"):
                    self.assertIs(step["with"]["persist-credentials"], False)

    def test_write_job_only_downloads_same_run_data_and_publishes_it(self):
        writer = WORKFLOW["jobs"]["coverage-badges"]
        commands = writer["steps"][1]["run"]
        self.assertNotIn("pdm ", commands, "The write-token job must not run repository Python")
        self.assertEqual(len(writer["steps"]), 2, "Only download and fixed publication belong here")
        self.assertNotIn("python", commands, "Repository code belongs in the read-only renderer")
        actions = [step["uses"] for step in writer["steps"] if "uses" in step]
        self.assertEqual(
            actions, ["actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"]
        )
        self.assertEqual(writer["permissions"], {"contents": "write"})
        download = next(step for step in writer["steps"] if "uses" in step)
        self.assertEqual(
            download["with"],
            {"name": "badge-data-${{ github.sha }}", "path": "tmp/coverage-badges"},
        )
        copies = [line for line in commands.splitlines() if line.startswith("cp ")]
        self.assertEqual(
            copies,
            [
                "cp tmp/coverage-badges/server.json tmp/coverage-badges/harness.json "
                'tmp/coverage-badges/provenance.json "$directory/"'
            ],
        )

    def test_renderer_is_read_only_and_exports_only_the_three_badge_files(self):
        jobs = WORKFLOW["jobs"]
        self.assertIn("coverage-badges-render", jobs, "Rendering needs its own read-only job")
        renderer = jobs["coverage-badges-render"]
        self.assertEqual(renderer.get("permissions", WORKFLOW["permissions"]), {"contents": "read"})
        self.assertNotIn("secrets.", json.dumps(renderer))
        self.assertEqual(set(renderer["needs"]), {"test", "coverage"})
        commands = [step["run"] for step in renderer["steps"] if "run" in step]
        self.assertEqual(
            commands, ["pdm run python scripts/coverage_badges.py tmp/coverage tmp/coverage-badges"]
        )
        artifact = next(
            step["with"]
            for step in renderer["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        self.assertEqual(artifact["name"], "badge-data-${{ github.sha }}")
        self.assertEqual(
            set(artifact["path"].splitlines()),
            {
                "tmp/coverage-badges/server.json",
                "tmp/coverage-badges/harness.json",
                "tmp/coverage-badges/provenance.json",
            },
        )
        self.assertEqual(artifact["if-no-files-found"], "error")
        self.assertEqual(artifact["retention-days"], 1)
