"""Local typography retains its reviewed upstream bytes and licence provenance."""

import hashlib
import json
import unittest
from pathlib import Path

from scripts.site_assets import FONT_FILES


class SiteAssetsTest(unittest.TestCase):
    def test_every_shipped_font_and_licence_matches_its_pinned_source_digest(self):
        root = Path("docs/site-assets/fonts")
        sources = json.loads((root / "sources.json").read_text())
        self.assertEqual(set(sources), set(FONT_FILES) - {"sources.json"})
        for name, source in sources.items():
            with self.subTest(name=name):
                self.assertRegex(
                    source["url"],
                    r"^https://raw\.githubusercontent\.com/google/fonts/[a-f0-9]{40}/",
                )
                self.assertEqual(
                    hashlib.sha256((root / name).read_bytes()).hexdigest(), source["sha256"]
                )
