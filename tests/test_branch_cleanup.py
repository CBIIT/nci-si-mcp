"""The post-merge process deletes only refs included in the reviewed milestone head."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "branch_cleanup.py"
WORKFLOW = ROOT / ".github" / "workflows" / "branch-cleanup.yml"
GIT = shutil.which("git")
FAKE_GH = """
import os
import subprocess
import sys

prefix = "repos/example/project/git/refs/heads/"
if sys.argv[1:4] != ["api", "-X", "DELETE"] or not sys.argv[4].startswith(prefix):
    sys.exit("Unexpected API request")
name = sys.argv[4].removeprefix(prefix)
if name == os.environ.get("FAIL_REF"):
    sys.exit("API deletion failed")
subprocess.run([os.environ["REAL_GIT"], "--git-dir", os.environ["BARE_REMOTE"],
                "update-ref", "-d", "refs/heads/" + name], check=True)
if name == "issue/a-merged" and os.environ.get("ADVANCE_REF"):
    subprocess.run([os.environ["REAL_GIT"], "--git-dir", os.environ["BARE_REMOTE"],
                    "update-ref", os.environ["ADVANCE_REF"], os.environ["ADVANCE_TIP"]],
                   check=True)
"""
FAKE_GIT = """
import os
import sys

if sys.argv[1] == "merge-base":
    sys.exit("Git ancestry failed")
os.execv(os.environ["REAL_GIT"], [os.environ["REAL_GIT"], *sys.argv[1:]])
"""


class BranchCleanupTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.remote = self.root / "remote.git"
        self.checkout = self.root / "checkout"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.summary = self.root / "summary.md"
        self.summary.touch()
        self.git("init", "--bare", str(self.remote))
        self.git("init", "-b", "main", str(self.checkout))
        self.git("config", "user.name", "Test runner", cwd=self.checkout)
        self.git("config", "user.email", "test@example.invalid", cwd=self.checkout)
        self.base = self.commit("base")
        self.head = self.commit("reviewed head")
        self.git("switch", "--detach", self.base, cwd=self.checkout)
        self.unmerged = self.commit("unmerged work")
        self.git("remote", "add", "origin", str(self.remote), cwd=self.checkout)
        self.git("push", "origin", "HEAD:refs/heads/main", cwd=self.checkout)
        self.git("push", "origin", f"{self.head}:refs/pull/42/head", cwd=self.checkout)
        self.refs = {
            "main": self.unmerged,
            "issue/a-merged": self.base,
            "issue/nested/merged": self.head,
            "issue/unmerged": self.unmerged,
            "feature/keep": self.head,
            "milestone/phase-test": self.head,
        }
        for name, tip in self.refs.items():
            self.set_ref(name, tip)
        self.executable("gh", FAKE_GH)

    def git(self, *args, cwd=None):
        return subprocess.check_output(  # noqa: S603 - real git in an isolated test repository
            [GIT, *args], cwd=cwd, text=True, stderr=subprocess.PIPE
        ).strip()

    def commit(self, message):
        self.git("commit", "--allow-empty", "-m", message, cwd=self.checkout)
        return self.git("rev-parse", "HEAD", cwd=self.checkout)

    def set_ref(self, name, tip):
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/" + name, tip)

    def remote_refs(self):
        rows = self.git(
            "--git-dir",
            str(self.remote),
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs/heads",
        )
        return dict(row.removeprefix("refs/heads/").split() for row in rows.splitlines())

    def executable(self, name, source):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + source)
        path.chmod(0o700)

    def cleanup(self, **overrides):
        env = (
            os.environ
            | {
                "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                "REAL_GIT": GIT,
                "BARE_REMOTE": str(self.remote),
                "GITHUB_REPOSITORY": "example/project",
                "GITHUB_STEP_SUMMARY": str(self.summary),
                "MERGED_HEAD": self.head,
                "MILESTONE_BRANCH": "milestone/phase-test",
                "PR_NUMBER": "42",
            }
            | overrides
        )
        return subprocess.run(  # noqa: S603 - production entry point against a local remote
            [sys.executable, str(SCRIPT)],
            cwd=self.checkout,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def test_merged_issues_are_deleted_and_unmerged_and_other_branches_remain(self):
        result = self.cleanup()
        self.assertEqual(
            self.remote_refs(),
            {
                "main": self.unmerged,
                "feature/keep": self.head,
                "issue/unmerged": self.unmerged,
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = self.summary.read_text()
        for name in ("issue/a-merged", "issue/nested/merged", "milestone/phase-test"):
            self.assertIn(f"| Deleted | <code>{name}</code> | {self.refs[name]} |", summary)
        self.assertIn(f"| Kept | <code>issue/unmerged</code> | {self.unmerged} |", summary)
        self.assertNotIn("feature/keep", summary)

    def test_missing_or_advanced_milestone_is_not_deleted(self):
        for tip in (None, self.unmerged):
            with self.subTest(tip=tip):
                if tip is None:
                    self.git(
                        "--git-dir",
                        str(self.remote),
                        "update-ref",
                        "-d",
                        "refs/heads/milestone/phase-test",
                    )
                else:
                    self.set_ref("milestone/phase-test", tip)
                result = self.cleanup()
                self.assertEqual(self.remote_refs().get("milestone/phase-test"), tip)
                self.assertNotIn("issue/a-merged", self.remote_refs())
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_branches_advanced_during_cleanup_are_kept_with_current_tips(self):
        for name in ("issue/nested/merged", "milestone/phase-test"):
            with self.subTest(name=name):
                self.set_ref("issue/a-merged", self.base)
                self.set_ref(name, self.head)
                result = self.cleanup(ADVANCE_REF="refs/heads/" + name, ADVANCE_TIP=self.unmerged)
                self.assertEqual(self.remote_refs()[name], self.unmerged)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(
                    f"| Kept | <code>{name}</code> | {self.unmerged} |", self.summary.read_text()
                )

    def test_api_failure_preserves_failed_branch_and_prior_deletion_summary(self):
        result = self.cleanup(FAIL_REF="issue/nested/merged")
        self.assertNotIn("issue/a-merged", self.remote_refs())
        self.assertEqual(self.remote_refs()["issue/nested/merged"], self.head)
        self.assertNotEqual(result.returncode, 0)
        summary = self.summary.read_text()
        self.assertIn(f"| Deleted | <code>issue/a-merged</code> | {self.base} |", summary)
        self.assertIn(f"| Failed | <code>issue/nested/merged</code> | {self.head} |", summary)
        self.assertNotIn("| Deleted | <code>issue/nested/merged", summary)

    def test_git_errors_and_wrong_pr_head_fail_without_deletion(self):
        for failure in ("git", "head"):
            with self.subTest(failure=failure):
                env = {"MERGED_HEAD": self.unmerged} if failure == "head" else {}
                if failure == "git":
                    self.executable("git", FAKE_GIT)
                result = self.cleanup(**env)
                self.assertEqual(self.remote_refs(), self.refs)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("| Deleted |", self.summary.read_text())
                if failure == "git":
                    (self.bin / "git").unlink()


class BranchCleanupWorkflowTest(unittest.TestCase):
    def test_only_merged_local_milestones_authorize_cleanup_from_merged_code(self):
        paths = list((ROOT / ".github" / "workflows").glob("branch-cleanup.yml"))
        self.assertEqual(
            paths, [WORKFLOW], "The merged-milestone cleanup workflow must be registered"
        )
        text = WORKFLOW.read_text()
        workflow = yaml.safe_load(text)
        self.assertEqual(
            workflow[True], {"pull_request": {"types": ["closed"], "branches": ["main"]}}
        )
        self.assertEqual(workflow["permissions"], {})
        job = workflow["jobs"]["cleanup"]
        self.assertEqual(job["permissions"], {"contents": "write"})
        condition = " ".join(job["if"].split())
        self.assertEqual(
            condition,
            "github.event.pull_request.merged == true && "
            "startsWith(github.event.pull_request.head.ref, 'milestone/') && "
            "github.event.pull_request.head.repo.full_name == github.repository",
        )
        checkout, cleanup = job["steps"]
        self.assertRegex(checkout["uses"], r"^actions/checkout@[0-9a-f]{40}$")
        self.assertRegex(text, r"actions/checkout@[0-9a-f]{40}\s+# v[0-9.]+")
        self.assertEqual(
            checkout["with"],
            {
                "ref": "${{ github.event.pull_request.merge_commit_sha }}",
                "fetch-depth": 0,
                "persist-credentials": False,
            },
        )
        self.assertEqual(cleanup["run"].strip(), "python3 scripts/branch_cleanup.py")
        self.assertEqual(
            cleanup["env"],
            {
                "GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
                "MERGED_HEAD": "${{ github.event.pull_request.head.sha }}",
                "MILESTONE_BRANCH": "${{ github.event.pull_request.head.ref }}",
                "PR_NUMBER": "${{ github.event.pull_request.number }}",
            },
        )
