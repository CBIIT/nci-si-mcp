"""Malformed publication evidence cannot produce attestation identities."""

import json
import os
import subprocess
import sys
import unittest
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

import test_image_publish

ROOT = Path(__file__).resolve().parents[1]


class ImageDigestEvidenceTest(unittest.TestCase):
    def test_module_entry_rejects_bad_tag_before_any_external_command(self):
        result = subprocess.run(
            [sys.executable, "-m", "scripts.image_publish", "--bad-tag"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Expected a release tag", result.stderr)

    def test_nonstring_digest_entries_do_not_hide_the_one_valid_image(self):
        with TemporaryDirectory() as directory, chdir(directory):
            publication = test_image_publish.ImagePublicationTest()
            publication.evidence()
            digest = "ghcr.io/new-org/project@sha256:" + "a" * 64
            with patch(
                "test_image_publish.PublicationService.run_image",
                return_value=json.dumps([None, digest]),
            ):
                publication.publish(public=True)
            self.assertIn("subject-digest=sha256:" + "a" * 64, Path("outputs").read_text())

    def test_bad_digest_cannot_write_attestation_outputs_or_upload_evidence(self):
        invalid = [
            [],
            None,
            ["ghcr.io/new-org/project@sha256:abc"],
            ["ghcr.io/other/project@sha256:" + "a" * 64],
            ["ghcr.io/new-org/project@sha256:" + "a" * 64] * 2,
            ["ghcr.io/new-org/project@sha256:" + "g" * 64],
            ["ghcr.io/new-org/project@sha256:" + "a" * 64 + "x"],
        ]
        for digests in invalid:
            with self.subTest(digests=digests), TemporaryDirectory() as directory, chdir(directory):
                publication = test_image_publish.ImagePublicationTest()
                publication.evidence()
                with (
                    patch(
                        "test_image_publish.PublicationService.run_image",
                        return_value=json.dumps(digests),
                    ),
                    self.assertRaisesRegex(ValueError, "digest"),
                ):
                    publication.publish(public=True)
                self.assertFalse(Path("outputs").exists())
                self.assertEqual(publication.service.assets, {})


class WorkflowGuardExecutionTest(unittest.TestCase):
    def execute_guard(self, validated: str, api_exit: int):
        value = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
        script = next(s["run"] for s in value["jobs"]["release"]["steps"] if s.get("id") == "guard")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name, body in {
                "git": 'print("a" * 40)',
                "python3": "import os,sys\n"
                "assert sys.argv[1:] == ['-m', 'scripts.release_ci', 'a' * 40], sys.argv\n"
                "sys.exit(int(os.environ['API_EXIT']))",
            }.items():
                executable = root / name
                executable.write_text(f"#!{sys.executable}\n{body}\n")
                executable.chmod(0o755)
            output = root / "output"
            result = subprocess.run(  # noqa: S603 - repository script, isolated executables
                ["/bin/bash", "-c", script],
                env={
                    "PATH": directory + os.pathsep + os.environ["PATH"],
                    "VALIDATED_SHA": validated,
                    "GITHUB_OUTPUT": str(output),
                    "API_EXIT": str(api_exit),
                    "GITHUB_REF": "refs/heads/main",
                    "GITHUB_SHA": "a" * 40,
                },
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            return result.returncode, output.read_text() if output.exists() else ""

    def test_manual_ci_failure_refuses_and_success_allows(self):
        self.assertEqual(self.execute_guard("", 1), (1, ""))
        self.assertEqual(self.execute_guard("", 0), (0, "skip=false\n"))

    def test_automatic_release_preserves_matching_sha_and_moved_main_behavior(self):
        self.assertEqual(self.execute_guard("a" * 40, 1), (0, "skip=false\n"))
        self.assertEqual(self.execute_guard("b" * 40, 1), (0, "skip=true\n"))
