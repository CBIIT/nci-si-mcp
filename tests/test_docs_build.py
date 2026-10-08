"""Exercise a complete static build, including search and excluded-file canaries."""

import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.docs_site import build_identity, build_site, main, source_identity

ROOT = Path(__file__).resolve().parents[1]


class DocumentationBuildTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        assets = self.root / "docs/site-assets"
        assets.mkdir(parents=True)
        for name in ("site.css", "main.html"):
            shutil.copyfile(ROOT / "docs/site-assets" / name, assets / name)
        vendor = assets / "node_modules/mermaid"
        (vendor / "dist").mkdir(parents=True)
        (vendor / "dist/mermaid.min.js").write_text("/* test runtime */")
        (vendor / "LICENSE").write_text("Test license")
        (assets / "zensical.toml").write_text(self.config())
        (self.root / "README.md").write_text("# Public overview\n\n[Story](docs/story.md)\n")
        (self.root / "docs/story.md").write_text("# A domain story\n\nRetrieved, not invented.\n")
        (self.root / "docs/private.md").write_text("PRIVATE-STORY-CANARY")
        (self.root / "docs/results.json").write_text("PRIVATE-RESULT-CANARY")
        self.identity = build_identity("a" * 40, "0.16.0", dirty=True)
        self.output = self.root / "published"

    def config(self):
        return """[project]
site_name = "Test documentation"
docs_dir = "content"
site_dir = "site"
use_directory_urls = false
nav = [{ Overview = "README.md" }, { Story = "docs/story.md" }]
[project.theme]
font = false
custom_dir = "overrides"
"""

    def build(self):
        with patch("scripts.docs_site.source_identity", return_value=self.identity):
            return build_site(self.root, self.output)

    def test_complete_artifact_has_public_story_search_and_no_excluded_content(self):
        self.build()
        page = (self.output / "index.html").read_text()
        self.assertIn("Public overview", page)
        self.assertIn('href="docs/story.html"', page)
        self.assertIn("modified source", page)
        self.assertEqual(json.loads((self.output / "build.json").read_text()), self.identity)
        files = [path for path in self.output.rglob("*") if path.is_file()]
        self.assertTrue(any("search" in str(path) for path in files))
        self.assertTrue((self.output / "docs/story.html").is_file())
        for path in files:
            with self.subTest(path=path.relative_to(self.output)):
                self.assertNotIn(b"PRIVATE-STORY-CANARY", path.read_bytes())
                self.assertNotIn(b"PRIVATE-RESULT-CANARY", path.read_bytes())

    def test_existing_destination_preserves_human_files(self):
        self.output.mkdir()
        (self.output / "keep.txt").write_text("Human work")
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual((self.output / "keep.txt").read_text(), "Human work")

    def test_broken_link_prevents_any_publication_and_cleans_staging(self):
        (self.root / "README.md").write_text("# Overview\n\n[Missing](unknown.py)\n")
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_invalid_tag_fails_before_git_and_never_becomes_release_identity(self):
        with self.assertRaises(ValueError):
            source_identity(self.root, "--help")

    def test_git_identity_keeps_installed_version_and_verified_tag_distinct(self):
        with (
            patch("scripts.docs_site._git", side_effect=["a" * 40, "a" * 40, ""]),
            patch("scripts.docs_site.version", return_value="0.16.0"),
        ):
            identity = source_identity(self.root, "v0.17.0")
        self.assertEqual(identity["release_tag"], "v0.17.0")
        self.assertEqual(identity["package_version"], "0.16.0")
        self.assertFalse(identity["dirty"])

    def test_local_preview_records_actual_git_source_state(self):
        identity = source_identity(ROOT, None)
        self.assertRegex(identity["source_commit"], r"^[a-f0-9]{40}$")
        self.assertEqual(identity["channel"], "development")
        self.assertIsNone(identity["release_tag"])

    def test_missing_git_is_actionable(self):
        with (
            patch("scripts.docs_site.shutil.which", return_value=None),
            self.assertRaisesRegex(FileNotFoundError, "Git is required"),
        ):
            source_identity(self.root, None)

    def test_external_asset_symlink_prevents_publication(self):
        asset = self.root / "docs/site-assets/site.css"
        asset.unlink()
        asset.symlink_to(ROOT / "docs/site-assets/site.css")
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_clean_verified_release_label_appears_in_generated_page(self):
        self.identity = build_identity(
            "a" * 40, "0.16.0", dirty=False, release_tag="v0.17.0", tag_commit="a" * 40
        )
        self.build()
        page = (self.output / "index.html").read_text()
        self.assertIn("v0.17.0", page)
        self.assertNotIn("modified source", page)

    def test_internal_symlinked_asset_directory_is_rejected(self):
        assets = self.root / "docs/site-assets"
        modules = assets / "node_modules"
        modules.rename(assets / "alternate-modules")
        modules.symlink_to(assets / "alternate-modules", target_is_directory=True)
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_cli_rejects_overwriting_an_existing_destination(self):
        self.output.mkdir()
        (self.output / "human.txt").write_text("Keep")
        with (
            patch("sys.argv", ["docs-build", "--output", str(self.output)]),
            self.assertRaises(FileExistsError),
        ):
            main()
        self.assertEqual((self.output / "human.txt").read_text(), "Keep")
