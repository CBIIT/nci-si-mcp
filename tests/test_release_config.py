"""The pull request title check accepts what the release configuration can read."""

import os
import re
import subprocess
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "pr-title.yml").read_text(encoding="utf-8")
# The shell script of the workflow's only step.
SCRIPT = textwrap.dedent(WORKFLOW.split("        run: |\n")[1])


class PullRequestTitleTest(unittest.TestCase):
    def check(self, title):
        """Run the title check as the workflow does; return its exit code and its summary."""

        with tempfile.NamedTemporaryFile() as summary:
            # The script comes from this repository, and bash is found on the PATH as on the runner.
            process = subprocess.run(  # noqa: S603
                ["bash", "-c", SCRIPT],  # noqa: S607
                env={
                    "PATH": os.environ["PATH"],
                    "TITLE": title,
                    "GITHUB_STEP_SUMMARY": summary.name,
                },
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            return process.returncode, Path(summary.name).read_text(encoding="utf-8")

    def test_title_check_accepts_exactly_the_types_the_release_parser_reads(self):
        with (ROOT / "pyproject.toml").open("rb") as file:
            options = tomllib.load(file)["tool"]["semantic_release"]["commit_parser_options"]

        accepted = re.search(r"pattern='\^\(([a-z|]+)\)", SCRIPT).group(1).split("|")

        self.assertCountEqual(accepted, options["allowed_tags"])
        self.assertLessEqual(
            set(options["minor_tags"] + options["patch_tags"]), set(options["allowed_tags"])
        )

    def test_conventional_titles_pass(self):
        for title in (
            "feat: add a tool",
            "fix(index): reject a vector of another dimension",
            "feat(server)!: rename the lookup tool",
            "build!: require Python 3.13",
            "security!: require a token",
        ):
            with self.subTest(title):
                self.assertEqual(self.check(title), (0, ""))

    def test_other_titles_fail_with_an_explanation(self):
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
                code, summary = self.check(title)

                self.assertEqual(code, 1)
                self.assertIn(title, summary)
                self.assertIn("CONTRIBUTING.md", summary)


if __name__ == "__main__":
    unittest.main()
