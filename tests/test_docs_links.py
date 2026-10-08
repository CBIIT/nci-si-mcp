"""Published pages keep local navigation and never fetch remote badge images."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.docs_links import rewrite_page


class DocumentationLinksTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.site = self.root / "site"
        self.site.mkdir()
        (self.site / "guide.html").write_text('<h1 id="start">Guide</h1>')
        self.source = self.root / "repo"
        self.source.mkdir()
        (self.source / "sample.py").write_text("print('example')")

    def render(self, html):
        return rewrite_page(html, self.site / "index.html", self.site, self.source, "a" * 40)

    def test_local_links_remain_relative_for_subpath_deployment(self):
        self.assertIn(
            'href="guide.html#start"', self.render('<a href="guide.html#start">Guide</a>')
        )

    def test_repository_source_link_is_bound_to_source_commit(self):
        result = self.render('<a href="sample.py">Example</a>')
        self.assertIn("https://github.com/CBIIT/nci-si-mcp/blob/" + "a" * 40 + "/sample.py", result)

    def test_remote_badge_becomes_text_without_network_image_dependency(self):
        result = self.render(
            '<img src="https://example.org/badge.svg" alt="Coverage &amp; checks">'
        )
        self.assertEqual(result, "Coverage &amp; checks")

    def test_unknown_local_link_and_remote_script_fail_closed(self):
        for html in (
            '<a href="missing.html">Missing</a>',
            '<script src="https://example.org/code.js"></script>',
        ):
            with self.subTest(html=html), self.assertRaises(ValueError):
                self.render(html)

    def test_escaped_path_and_javascript_link_are_rejected(self):
        for href in ("../outside.html", "javascript:alert(1)", "/guide.html"):
            with self.subTest(href=href), self.assertRaises(ValueError):
                self.render(f'<a href="{href}">Link</a>')

    def test_missing_fragment_is_rejected(self):
        with self.assertRaises(ValueError):
            self.render('<a href="guide.html#absent">Missing heading</a>')

    def test_inline_script_and_entities_survive_processing(self):
        html = "<!-- context --><script>const ok = 1 < 2;</script><p>A &amp; B &#169;</p>"
        self.assertEqual(self.render(html), html)

    def test_directory_and_excluded_document_links_resolve_without_copying_content(self):
        (self.source / "records").mkdir()
        (self.source / "decision.md").write_text("# Decision")
        result = self.render(
            '<a href="records/index.html">Records</a><a href="decision.html#decision">Decision</a>'
        )
        self.assertIn("/tree/" + "a" * 40 + "/records", result)
        self.assertIn("/blob/" + "a" * 40 + "/decision.md#decision", result)
        self.assertFalse((self.site / "decision.md").exists())

    def test_local_directory_navigation_and_missing_assets(self):
        (self.site / "nested").mkdir()
        (self.site / "nested/index.html").write_text("<h1>Nested</h1>")
        self.assertIn('href="nested/"', self.render('<a href="nested/">Nested</a>'))
        with self.assertRaises(ValueError):
            self.render('<script src="missing.js"></script>')
