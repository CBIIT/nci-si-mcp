"""Validation jobs execute committed source, not mutable working-tree files or local secrets."""

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.operator_source import SOURCE_PATHS, archive_source, head_commit, package_source


class OperatorSourceTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        for name in SOURCE_PATHS:
            path = self.root / name
            if "." not in name:
                path = path / "tracked.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("committed")
        self.git("add", ".")
        self.commit()
        self.output = Path(temporary.name) / "source"

    def git(self, *arguments):
        return subprocess.run(  # noqa: S603 - fixed Git operations in the owned test repository
            ["git", *arguments],  # noqa: S607 - local Git executable
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit(self):
        self.git(
            "-c",
            "user.name=Test role",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "test: snapshot",
        )

    def test_snapshot_binds_commit_and_excludes_local_edits_and_private_files(self):
        commit = head_commit(self.root)
        (self.root / "src/tracked.txt").write_text("uncommitted")
        (self.root / ".env").write_text("PRIVATE-CANARY")
        archive_source(self.root, commit, self.output)
        self.assertEqual((self.output / "src/tracked.txt").read_text(), "committed")
        self.assertFalse((self.output / ".env").exists())
        self.assertFalse((self.output / ".git").exists())
        self.assertEqual(commit, self.git("rev-parse", "HEAD"))

    def test_tracked_symlink_is_rejected_without_following_it(self):
        outside = self.root.parent / "private.txt"
        outside.write_text("PRIVATE-CANARY")
        (self.root / "src/link").symlink_to(outside)
        self.git("add", "src/link")
        self.commit()
        with self.assertRaisesRegex(ValueError, "regular files"):
            archive_source(self.root, head_commit(self.root), self.output)
        self.assertEqual(outside.read_text(), "PRIVATE-CANARY")
        self.assertFalse((self.output / "src/link").exists())

    def test_invalid_or_missing_commit_never_extracts_an_unpinned_snapshot(self):
        for commit in ("HEAD", "a" * 40):
            with self.subTest(commit=commit), self.assertRaises(ValueError):
                archive_source(self.root, commit, self.output)
        self.assertFalse(self.output.exists())

    def test_source_size_limit_rejects_archive_without_partial_output(self):
        with (
            patch("scripts.operator_source.MAX_SOURCE_BYTES", 10),
            self.assertRaisesRegex(ValueError, "size bound"),
        ):
            archive_source(self.root, head_commit(self.root), self.output)
        self.assertFalse(self.output.exists())

    def test_image_source_runs_without_git_and_excludes_uncommitted_private_content(self):
        (self.root / ".env").write_text("PRIVATE-CANARY")
        (self.root / "src/tracked.txt").write_text("uncommitted")
        image = self.root.parent / "image"
        commit = package_source(self.root, image)
        with patch(
            "scripts.operator_source.subprocess.run", side_effect=AssertionError("No Git in image")
        ):
            self.assertEqual(head_commit(image), commit)
            archive_source(image, commit, self.output)
        self.assertEqual((self.output / "src/tracked.txt").read_text(), "committed")
        self.assertFalse((self.output / ".env").exists())
        self.assertFalse((image / ".git").exists())

    def test_image_archive_tampering_is_rejected_before_creating_a_workspace(self):
        image = self.root.parent / "image"
        commit = package_source(self.root, image)
        (image / "operator-source.tar").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            head_commit(image)
        with self.assertRaises(ValueError):
            archive_source(image, commit, self.output)
        self.assertFalse(self.output.exists())

    def test_image_cannot_claim_another_commit_or_execute_a_different_selection(self):
        image = self.root.parent / "image"
        commit = package_source(self.root, image)
        with self.assertRaises(ValueError):
            archive_source(image, "a" * 40, self.output)
        path = image / "operator-source.json"
        manifest = json.loads(path.read_text())
        manifest["commit"] = "a" * 40
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            head_commit(image)
        self.assertNotEqual(commit, "a" * 40)
        self.assertFalse(self.output.exists())
