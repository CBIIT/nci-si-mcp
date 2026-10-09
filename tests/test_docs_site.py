"""Public documentation is assembled from reviewed sources, never a results directory."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.docs_site import build_identity, stage_pages


class SiteStagingTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "docs").mkdir()
        (self.root / "README.md").write_text("# Public overview\n")
        (self.root / "docs/guide.md").write_text("# Domain guide\n")
        (self.root / "docs/result.json").write_text("PRIVATE-RESULT-CANARY")
        (self.root / "docs/extra.md").write_text("UNREVIEWED-CANARY")
        self.pages = ["README.md", "docs/guide.md"]
        self.destination = self.root / "stage"

    def test_only_reviewed_pages_enter_fresh_staging(self):
        stage_pages(self.root, self.destination, self.pages)
        self.assertEqual((self.destination / "README.md").read_text(), "# Public overview\n")
        self.assertEqual((self.destination / "docs/guide.md").read_text(), "# Domain guide\n")
        self.assertFalse((self.destination / "docs/result.json").exists())
        self.assertFalse((self.destination / "docs/extra.md").exists())

    def test_existing_output_is_not_reused_or_recursively_deleted(self):
        self.destination.mkdir()
        sentinel = self.destination / "human.txt"
        sentinel.write_text("KEEP")
        with self.assertRaises(FileExistsError):
            stage_pages(self.root, self.destination, self.pages)
        self.assertEqual(sentinel.read_text(), "KEEP")

    def test_symlinked_page_and_parent_directory_are_rejected(self):
        (self.root / "linked.md").symlink_to(self.root / "README.md")
        (self.root / "alias").symlink_to(self.root / "docs", target_is_directory=True)
        for name in ("linked.md", "alias/guide.md"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                stage_pages(self.root, self.destination, [name])
            self.assertFalse(self.destination.exists())

    def test_absolute_escape_and_non_document_paths_are_rejected_before_writes(self):
        for name in ("../outside.md", str(self.root / "README.md"), "docs/result.json"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                stage_pages(self.root, self.destination, [name])
            self.assertFalse(self.destination.exists())

    def test_duplicate_or_missing_pages_do_not_leave_partial_staging(self):
        for pages in (["README.md", "README.md"], ["README.md", "missing.md"], []):
            with self.subTest(pages=pages), self.assertRaises((ValueError, FileNotFoundError)):
                stage_pages(self.root, self.destination, pages)
            self.assertFalse(self.destination.exists())

    def test_development_identity_does_not_relabel_installed_version_as_a_release(self):
        identity = build_identity("a" * 40, "0.16.0", dirty=True)
        self.assertEqual(identity["channel"], "development")
        self.assertEqual(identity["package_version"], "0.16.0")
        self.assertIsNone(identity["release_tag"])
        self.assertTrue(identity["dirty"])

    def test_release_label_requires_clean_tree_and_matching_tag_commit(self):
        for dirty, target in ((True, "a" * 40), (False, "b" * 40), (False, None)):
            with self.subTest(dirty=dirty, target=target), self.assertRaises(ValueError):
                build_identity(
                    "a" * 40, "0.16.0", dirty=dirty, release_tag="v0.17.0", tag_commit=target
                )

    def test_verified_tag_is_separate_from_installed_package_version(self):
        identity = build_identity(
            "a" * 40, "0.16.0", dirty=False, release_tag="v0.17.0", tag_commit="a" * 40
        )
        self.assertEqual(identity["channel"], "release")
        self.assertEqual(identity["release_tag"], "v0.17.0")
        self.assertEqual(identity["package_version"], "0.16.0")
        self.assertEqual(json.loads(json.dumps(identity)), identity)

    def test_untrusted_version_labels_cannot_enter_site_markup(self):
        for source, version in (("<script>", "0.16.0"), ("a" * 40, "<script>")):
            with self.subTest(source=source), self.assertRaises(ValueError):
                build_identity(source, version, dirty=False)

    def test_oversized_or_invalid_utf8_page_is_rejected_before_staging(self):
        with patch("scripts.docs_site.MAX_PAGE_BYTES", 8), self.assertRaises(ValueError):
            stage_pages(self.root, self.destination, self.pages)
        (self.root / "README.md").write_bytes(b"\xff")
        with self.assertRaises(UnicodeDecodeError):
            stage_pages(self.root, self.destination, self.pages)
        self.assertFalse(self.destination.exists())

    def test_inconsistent_identity_metadata_is_rejected(self):
        for options in (
            {"dirty": 1},
            {"dirty": False, "tag_commit": "a" * 40},
            {"dirty": False, "release_tag": "latest"},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                build_identity("a" * 40, "0.16.0", **options)
