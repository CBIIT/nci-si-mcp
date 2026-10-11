"""Image selection fails safe on the event's actual Git change set."""

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml
from scripts import ci_images

ROOT = Path(__file__).resolve().parents[1]


class ImageWorkflowTest(unittest.TestCase):
    def test_selector_installs_its_python_runtime_before_executing(self):
        jobs = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())["jobs"]
        steps = jobs["changes"]["steps"]
        setup = [step for step in steps if "setup-python@" in step.get("uses", "")]
        self.assertEqual(len(setup), 1, "The selector requires Python 3.14, not runner Python")
        self.assertEqual(setup[0]["with"]["python-version"], "3.14")
        command = next(step for step in steps if step.get("id") == "select")
        self.assertLess(steps.index(setup[0]), steps.index(command))

    def test_same_named_image_jobs_use_event_sha_selection_and_main_always_runs(self):
        jobs = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())["jobs"]
        self.assertIn("changes", jobs, "PR images need fail-safe change selection")
        selector = jobs["changes"]
        checkout = next(
            step
            for step in selector["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        )
        self.assertEqual(checkout["with"]["fetch-depth"], 0)
        step = next(step for step in selector["steps"] if step.get("id") == "select")
        self.assertEqual(step["id"], "select")
        self.assertEqual(
            step["run"],
            'python3 -m scripts.ci_images "$EVENT_NAME" "$BASE_SHA" "$HEAD_SHA" '
            '>> "$GITHUB_OUTPUT"',
        )
        self.assertEqual(
            step["env"],
            {
                "EVENT_NAME": "${{ github.event_name }}",
                "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
                "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
            },
        )
        self.assertEqual(jobs["image"]["needs"], ["changes"])
        self.assertEqual(
            jobs["image"]["if"],
            "github.event_name != 'pull_request' || needs.changes.outputs.image == 'true'",
        )
        for job, name in (
            ("image", "container (amd64)"),
            ("companions", "companion containers (amd64)"),
        ):
            self.assertEqual(jobs[job]["name"], name)
            self.assertEqual(selector["outputs"][job], "${{ steps.select.outputs." + job + " }}")


class ImageSelectionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Test role")
        self.git("config", "user.email", "test@example.invalid")
        self.commit("initial.txt")

    def git(self, *arguments):
        return subprocess.check_output(  # noqa: S603 - fixed Git operations in the owned repository
            [shutil.which("git"), *arguments], cwd=self.root, text=True, stderr=subprocess.PIPE
        ).strip()

    def commit(self, path):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed\n")
        self.git("add", "--", path)
        self.git("commit", "-m", "test: change input")
        return self.git("rev-parse", "HEAD")

    def selected(self, base, head, event="pull_request"):
        result = subprocess.run(  # noqa: S603 - real local entry point, fixed arguments, no shell
            [sys.executable, "-P", "-m", "scripts.ci_images", event, base, head],
            cwd=self.root,
            env={"PATH": os.environ["PATH"], "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        decisions = dict(line.split("=", 1) for line in result.stdout.splitlines())
        return decisions, result

    def assert_selection(self, base, head, image, companions, event="pull_request"):
        decisions, result = self.selected(base, head, event)
        self.assertEqual(decisions, {"image": image, "companions": companions}, result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_inputs_select_both_and_only_known_noninputs_skip(self):
        cases = (
            ("new_runtime/input.bin", "true", "true"),
            ("tests/test_new.py", "false", "false"),
            ("acceptance/selftests/test_new.py", "false", "true"),
            (".github/ISSUE_TEMPLATE/new.yml", "false", "false"),
            (".github/PULL_REQUEST_TEMPLATE.md", "false", "false"),
            (".github/PULL_REQUEST_TEMPLATE/other.md", "false", "false"),
            ("acceptance/src/x.py", "true", "true"),
            ("docs/site-assets/x.js", "true", "true"),
            ("AGENTS.md", "false", "true"),
            ("README.md", "true", "true"),
            ("QUICKSTART.md", "false", "true"),
            ("CONTRIBUTING.md", "false", "true"),
            ("ARCHITECTURE.md", "false", "true"),
            ("docs/new-guide.md", "false", "true"),
            ("src/new_package/README.md", "true", "true"),
            ("scripts/ci_images.py", "true", "true"),
            (".github/workflows/ci.yml", "true", "true"),
        )
        for path, image, companions in cases:
            with self.subTest(path=path):
                base = self.git("rev-parse", "HEAD")
                head = self.commit(path)
                self.assert_selection(base, head, image, companions)

    def test_whole_event_range_not_only_last_commit_selects_inputs(self):
        base = self.git("rev-parse", "HEAD")
        self.commit("new_runtime/input.bin")
        head = self.commit("tests/test_later.py")
        self.assert_selection(base, head, "true", "true")

    def test_base_only_changes_are_not_part_of_the_pull_request(self):
        self.git("checkout", "-b", "topic")
        head = self.commit("tests/topic.py")
        self.git("checkout", "main")
        base = self.commit("runtime.py")
        self.assert_selection(base, head, "false", "false")

    def test_non_full_sha_inputs_fail_safe_without_creating_output_files(self):
        base = self.git("rev-parse", "HEAD")
        middle = self.commit("tests/middle.py")
        head = self.commit("tests/head.py")
        self.git("branch", middle + "x", middle)
        for invalid in ("HEAD~1", middle[:7], middle + "x", "--output=selector-output"):
            for start, end in ((invalid, head), (base, invalid)):
                with self.subTest(base=start, head=end):
                    before = set(self.root.iterdir())
                    self.assert_selection(start, end, "true", "true")
                    self.assertEqual(set(self.root.iterdir()), before)

    def test_renames_count_both_paths_and_deletions_still_select(self):
        base = self.commit("runtime.bin")
        (self.root / "tests").mkdir()
        self.git("mv", "runtime.bin", "tests/moved.bin")
        self.git("commit", "-m", "test: move runtime into ignored path")
        head = self.git("rev-parse", "HEAD")
        self.assert_selection(base, head, "true", "true")
        base = self.commit("another-runtime.bin")
        self.git("rm", "another-runtime.bin")
        self.git("commit", "-m", "test: remove runtime")
        self.assert_selection(base, self.git("rev-parse", "HEAD"), "true", "true")

    def test_empty_or_unavailable_diff_and_main_select_both(self):
        base = self.git("rev-parse", "HEAD")
        head = self.commit("tests/test_only.py")
        for event, start, end in (
            ("pull_request", head, head),
            ("pull_request", "0" * 40, head),
            ("pull_request", base, "0" * 40),
            ("push", base, head),
        ):
            with self.subTest(event=event, base=start, head=end):
                self.assert_selection(start, end, "true", "true", event)


class ChangePathFailureTest(unittest.TestCase):
    def test_git_timeout_reports_no_trustworthy_change_set(self):
        with patch.object(
            ci_images.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 30)
        ):
            self.assertEqual(ci_images.changed_paths("a" * 40, "b" * 40), [])
