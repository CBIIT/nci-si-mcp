"""Validation jobs execute committed source, not mutable working-tree files or local secrets."""

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.operator_source import SOURCE_PATHS, archive_source, head_commit


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
