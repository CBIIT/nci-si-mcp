"""The shared lock helpers: PDM versions by group, name form and the continuation line."""

import unittest

from scripts.requirements_lock import normalize, pdm_versions, requirement_line


class RequirementsLockTest(unittest.TestCase):
    def test_each_hash_goes_on_its_own_continuation_line(self):
        self.assertEqual(
            requirement_line("a==1.0", ["sha256:" + "1" * 64, "sha256:" + "2" * 64]),
            "a==1.0 \\\n    --hash=sha256:" + "1" * 64 + " \\\n    --hash=sha256:" + "2" * 64,
        )

    def test_names_are_normalized_like_pip_does(self):
        self.assertEqual(normalize("Sentence_Transformers.X"), "sentence-transformers-x")

    def test_only_packages_of_the_requested_groups_are_pinned(self):
        server = pdm_versions({"server"})
        self.assertRegex(server["mcp"], r"^2\.")
        self.assertNotIn("ruff", server)
        self.assertIn("ruff", pdm_versions({"lint"}))
        self.assertEqual(pdm_versions(set()), {})
