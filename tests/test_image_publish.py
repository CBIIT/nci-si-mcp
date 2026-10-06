import io
import json
import subprocess
import tempfile
import unittest
from contextlib import chdir, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import image_publish


class ImagePublicationTest(unittest.TestCase):
    def publish(self, *, public):
        digest = "ghcr.io/new-org/project@sha256:" + "a" * 64

        def command(*args):
            if args[:3] == ("docker", "image", "inspect"):
                return json.dumps([digest])
            if args[:2] == ("docker", "--config") and not public:
                raise subprocess.CalledProcessError(1, "anonymous manifest check")
            if args[:3] == ("gh", "release", "view"):
                return json.dumps({"body": "Release description\n"})
            return ""

        env = {
            "GITHUB_REPOSITORY": "New-Org/Project",
            "GITHUB_ACTOR": "release-bot",
            "GH_TOKEN": "test-token",
        }
        output = io.StringIO()
        with (
            patch.dict("os.environ", env),
            patch("sys.argv", ["image_publish.py", "v0.15.0"]),
            patch.object(image_publish, "run", side_effect=command),
            patch.object(image_publish.subprocess, "run"),
            redirect_stdout(output),
        ):
            image_publish.main()
        return digest, output.getvalue()

    def test_transferred_repository_digest_and_reports_are_named_in_notes(self):
        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            evidence = Path("tmp/image")
            evidence.mkdir(parents=True)
            digest, output = self.publish(public=True)
            notes = (evidence / "release-notes.md").read_text()
            self.assertTrue(notes.startswith("Release description"))
            self.assertIn(digest, notes)
            self.assertIn("sbom.cdx.json", notes)
            self.assertIn("scan.json", notes)
            self.assertEqual((evidence / "image-digest.txt").read_text(), digest + "\n")
            self.assertIn("verified public image", output)
            self.assertNotIn("test-token", output + notes)

    def test_private_package_cannot_be_reported_as_public(self):
        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            Path("tmp/image").mkdir(parents=True)
            with self.assertRaises(subprocess.CalledProcessError):
                self.publish(public=False)
            self.assertFalse(Path("tmp/image/release-notes.md").exists())

    def test_invalid_tag_fails_before_publication(self):
        with (
            patch("sys.argv", ["image_publish.py", "--bad-tag"]),
            self.assertRaisesRegex(ValueError, "release tag"),
        ):
            image_publish.main()
