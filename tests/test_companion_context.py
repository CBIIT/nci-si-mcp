"""Companion image inputs must exclude checkout credentials and operational evidence."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.companion_context import prepare_context
from scripts.operator_source import head_commit


class CompanionContextTest(unittest.TestCase):
    def test_dependency_lock_drift_does_not_produce_an_image_context(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "container").mkdir()
            (root / "pdm.lock").write_bytes(Path("pdm.lock").read_bytes())
            (root / "container/companion-requirements.txt").write_text("stale dependencies")
            output = root / "context"
            with (
                patch("scripts.companion_context.clean_commit", return_value="a" * 40),
                self.assertRaisesRegex(ValueError, "lock drifted"),
            ):
                prepare_context(root, output)
            self.assertFalse(output.exists())

    @staticmethod
    def site(_root, output):
        output.mkdir()
        (output / "index.html").write_text("Public documentation")

    @staticmethod
    def wheels(_root, output):
        output.mkdir()
        (output / "runtime.whl").write_bytes(b"built package")

    def test_only_fixed_runtime_paths_and_public_site_enter_separate_contexts(self):
        root = Path.cwd()
        commit = head_commit(root)
        with TemporaryDirectory() as directory:
            output = Path(directory) / "context"
            with (
                patch("scripts.companion_context.clean_commit", return_value=commit),
                patch("scripts.companion_context.build_site", side_effect=self.site),
                patch("scripts.companion_context._wheels", side_effect=self.wheels),
            ):
                prepare_context(root, output)
            manifest = json.loads((output / "admin/bundle/operator-source.json").read_text())
            self.assertEqual(manifest["commit"], commit)
            self.assertTrue((output / "admin/app/src/nci_si_mcp/cli.py").is_file())
            self.assertFalse((output / "admin/app/.git").exists())
            self.assertFalse((output / "admin/app/tmp").exists())
            self.assertEqual(
                (output / "admin/app/docs/site-assets/fonts/open-sans.ttf").read_bytes(),
                (root / "docs/site-assets/fonts/open-sans.ttf").read_bytes(),
            )
            self.assertFalse((output / "admin/app/docs/upstream").exists())
            self.assertEqual((output / "docs/site/index.html").read_text(), "Public documentation")
            self.assertEqual(
                {p.name for p in (output / "docs").iterdir()},
                {"site", "Dockerfile", "static_server.py", "companion_relay.py"},
            )

    def test_source_change_during_build_leaves_no_publishable_context(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "context"
            with (
                patch("scripts.companion_context.clean_commit", side_effect=["a" * 40, "b" * 40]),
                patch("scripts.companion_context._populate"),
                self.assertRaisesRegex(ValueError, "changed"),
            ):
                prepare_context(Path.cwd(), output)
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_dirty_checkout_is_rejected_before_context_creation(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "context"
            with (
                patch("scripts.companion_context.clean_commit", side_effect=ValueError("dirty")),
                self.assertRaisesRegex(ValueError, "dirty"),
            ):
                prepare_context(Path.cwd(), output)
            self.assertFalse(output.exists())

    def test_existing_output_preserves_human_content(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "keep").write_text("human")
            with self.assertRaises(FileExistsError):
                prepare_context(Path.cwd(), output)
            self.assertEqual((output / "keep").read_text(), "human")
