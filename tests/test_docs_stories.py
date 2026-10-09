"""Website stories remain complete while readers browse bounded domain pages."""

import re
import unittest
from pathlib import Path

from scripts.docs_stories import partition_catalogue


class StoryPagesTest(unittest.TestCase):
    def setUp(self):
        self.source = Path("docs/behavioural-tests.md").read_text()

    def test_each_exact_case_is_preserved_once_outside_the_overview(self):
        pages = partition_catalogue(self.source)
        cases = re.findall(r"<code>(.*?)</code>", self.source)
        published = re.findall(r"<code>(.*?)</code>", "".join(pages.values()))
        self.assertCountEqual(published, cases)
        self.assertNotIn("<code>", pages["behavioural-tests.md"])
        self.assertEqual(len(pages), 48)

    def test_story_has_context_return_links_and_collapsed_executable_evidence(self):
        pages = partition_catalogue(self.source)
        story = pages["story-concept-detail.md"]
        self.assertIn("**User goal:**", story)
        self.assertIn("**Given:**", story)
        self.assertIn("**When:**", story)
        self.assertIn("**Then:**", story)
        self.assertIn("<details>", story)
        self.assertNotIn("<details open", story)
        self.assertIn("stories-terminology.md", story)
        self.assertIn("behavioural-tests.md", story)
        self.assertIn("../acceptance/tests/test_evs", story)

    def test_unknown_or_duplicate_story_fails_instead_of_disappearing(self):
        for source in (
            self.source.replace('id="concept-detail"', 'id="new-story"'),
            self.source + self.source[self.source.index('<a id="concept-detail"') :],
        ):
            with self.subTest(source=source[-30:]), self.assertRaises(ValueError):
                partition_catalogue(source)

    def test_missing_narrative_or_inconsistent_case_counts_prevent_publication(self):
        for before, after in (
            ("Exact executable cases (8)", "Exact executable cases (9)"),
            ("Exact executable cases (8)", "Cases unavailable"),
            ("**User goal:**", "**Missing goal:**"),
            ("## Discover a dependable", "Missing heading: Discover a dependable"),
            ("MCP acceptance cases,", "Missing inventory,"),
        ):
            with self.subTest(before=before):
                self.assertIn(before, self.source)
                with self.assertRaises(ValueError):
                    partition_catalogue(self.source.replace(before, after, 1))

    def test_existing_story_anchors_remain_links_to_the_new_pages(self):
        overview = partition_catalogue(self.source)["behavioural-tests.md"]
        for key in re.findall(r'<a id="([a-z-]+)"></a>', self.source):
            with self.subTest(key=key):
                self.assertIn(f'id="{key}"', overview)
                self.assertIn(f'href="story-{key}.html"', overview)
