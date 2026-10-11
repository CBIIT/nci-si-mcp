"""The context command reuses only a clean, same-commit public site."""

import io
import json
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import companion_context
from scripts.operator_source import head_commit

import test_companion_context


class PrebuiltSiteTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.site = self.root / "site"
        self.site.mkdir()
        self.commit = head_commit(Path.cwd())
        self.identity = {"schema": 1, "source_commit": self.commit, "dirty": False}
        (self.site / "build.json").write_text(json.dumps(self.identity))
        (self.site / "index.html").write_text("Already browser-tested public site")
        self.output = self.root / "context"

    def command(self, site=None):
        arguments = [
            "companion_context",
            "--site",
            str(site or self.site),
            "--output",
            str(self.output),
        ]
        diagnostics = io.StringIO()
        with (
            patch("sys.argv", arguments),
            patch.object(companion_context, "clean_commit", return_value=self.commit),
            patch.object(
                companion_context, "_wheels", test_companion_context.CompanionContextTest.wheels
            ),
            redirect_stderr(diagnostics),
        ):
            try:
                companion_context.main()
            except (SystemExit, ValueError, OSError) as error:
                return str(error) + diagnostics.getvalue()
        return ""

    def test_command_copies_the_prebuilt_site_without_rebuilding(self):
        error = self.command()
        marker = self.output / "docs/site/index.html"
        self.assertTrue(marker.is_file(), error)
        self.assertEqual(marker.read_text(), "Already browser-tested public site")
        self.assertEqual(
            json.loads((self.output / "docs/site/build.json").read_text()), self.identity
        )
        self.assertEqual(error, "")

    def test_wrong_or_dirty_source_identity_produces_no_context(self):
        for index, changed in enumerate(({"source_commit": "0" * 40}, {"dirty": True})):
            with self.subTest(changed=changed):
                self.output = self.root / f"context-{index}"
                (self.site / "build.json").write_text(json.dumps(self.identity | changed))
                error = self.command()
                self.assertFalse(self.output.exists())
                self.assertIn("clean source commit", error)

    def test_missing_site_is_not_silently_rebuilt(self):
        error = self.command(self.root / "missing")
        self.assertFalse(self.output.exists())
        self.assertIn("Prebuilt site", error)

    def test_unrecorded_clean_state_produces_no_context(self):
        missing = {key: value for key, value in self.identity.items() if key != "dirty"}
        for index, identity in enumerate((missing, self.identity | {"dirty": None})):
            with self.subTest(identity=identity):
                self.output = self.root / f"unrecorded-{index}"
                (self.site / "build.json").write_text(json.dumps(identity))
                error = self.command()
                self.assertFalse(self.output.exists())
                self.assertIn("clean source commit", error)

    def test_symlinked_site_or_descendant_is_not_copied(self):
        linked = self.root / "linked"
        linked.symlink_to(self.site, target_is_directory=True)
        for site in (linked, self.site):
            with self.subTest(site=site):
                self.output = self.root / f"context-{site.name}"
                if site == self.site:
                    (self.site / "nested").mkdir()
                    (self.site / "nested/linked.html").symlink_to(self.site / "index.html")
                error = self.command(site)
                self.assertFalse(self.output.exists())
                self.assertIn("symlink", error)
