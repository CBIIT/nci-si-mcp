"""The pull request title check accepts what the release configuration can read."""

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "pr-title.yml").read_text(encoding="utf-8")
PATTERN = re.search(r"pattern='(.+)'", WORKFLOW).group(1)


class PullRequestTitleTest(unittest.TestCase):
    def test_title_check_accepts_exactly_the_configured_commit_types(self):
        with (ROOT / "pyproject.toml").open("rb") as file:
            options = tomllib.load(file)["tool"]["semantic_release"]["commit_parser_options"]
        configured = options["minor_tags"] + options["patch_tags"] + options["other_allowed_tags"]

        accepted = re.match(r"\^\(([a-z|]+)\)", PATTERN).group(1).split("|")

        self.assertCountEqual(accepted, configured)

    def test_conventional_titles_pass(self):
        for title in (
            "feat: add a tool",
            "fix(index): reject a vector of another dimension",
            "feat(server)!: rename the lookup tool",
            "build!: require Python 3.13",
        ):
            with self.subTest(title):
                self.assertRegex(title, PATTERN)

    def test_other_titles_fail(self):
        for title in (
            "Fix something",
            "feature: add a tool",
            "fix:missing space",
            "fix: ",
            "Fix: wrong case",
            "fix(Index): upper-case scope",
            "wip fix: not at the start",
        ):
            with self.subTest(title):
                self.assertNotRegex(title, PATTERN)


if __name__ == "__main__":
    unittest.main()
