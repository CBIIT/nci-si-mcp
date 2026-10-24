"""Release execution stays locked, permission-bounded and independent of global Git identity."""

import os
import re
import shutil
import subprocess
import sys
import tomllib
import unittest
import venv
from pathlib import Path
from tempfile import TemporaryDirectory

import tomli_w
import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / ".github/workflows/release.yml"
CLI = (
    "pdm run semantic-release -v version --no-commit --tag --push "
    "--no-changelog --vcs-release --skip-build"
)
INSTALL = (
    "pdm lock --check\n"
    "pdm sync --no-default --no-self -G release --without server --without index\n"
)


def workflow():
    return yaml.safe_load(RELEASE.read_text(encoding="utf-8"))


def git(root, *arguments, env=None):
    return subprocess.check_output(  # noqa: S603 - fixed Git operations in an owned repository.
        ["git", *arguments],  # noqa: S607 - Git is the tested developer boundary.
        cwd=root,
        env=env,
        text=True,
        stderr=subprocess.PIPE,
    ).strip()


class ReleaseWorkflowTest(unittest.TestCase):
    def test_release_job_keeps_its_exact_security_and_output_contract(self):
        data = workflow()
        job = data["jobs"]["release"]
        self.assertEqual(data["permissions"], {})
        self.assertEqual(
            data["concurrency"], {"group": "release-main", "cancel-in-progress": False}
        )
        self.assertEqual(job["permissions"], {"contents": "write"})
        self.assertEqual(
            job["outputs"],
            {
                "image_tag": "${{ steps.image.outputs.tag }}",
                "image_sha": "${{ steps.image.outputs.sha }}",
            },
        )
        self.assertEqual(
            job["if"],
            "github.event_name == 'workflow_dispatch' || "
            "(github.event.workflow_run.conclusion == 'success' &&\n "
            "github.event.workflow_run.event == 'push' &&\n "
            "github.event.workflow_run.head_branch == 'main')",
        )

    def test_release_steps_use_only_the_locked_cli(self):
        steps = workflow()["jobs"]["release"]["steps"]
        selected = [
            step
            for step in steps
            if step.get("name")
            in {"Set up release tooling", "Install locked release group", "Semantic release"}
        ]
        self.assertEqual(
            selected,
            [
                {
                    "name": "Set up release tooling",
                    "if": "steps.guard.outputs.skip == 'false'",
                    "uses": "$/.github/actions/setup",
                    "with": {"release-only": "true"},
                },
                {
                    "name": "Semantic release",
                    "id": "release",
                    "if": "steps.guard.outputs.skip == 'false'",
                    "env": {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"},
                    "run": CLI,
                },
            ],
        )
        self.assertEqual(
            [step["name"] for step in steps],
            [
                "Check out main",
                "Decide whether this commit is released",
                "Set up release tooling",
                "Semantic release",
                "Summary",
                "Select the exact released commit for image publication",
            ],
        )
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(
            project["dependency-groups"]["release"], ["python-semantic-release==10.7.0"]
        )

    def test_setup_composite_isolates_release_installation_and_cache(self):
        setup = yaml.safe_load((ROOT / ".github/actions/setup/action.yml").read_text())
        self.assertEqual(
            setup["inputs"],
            {
                "release-only": {
                    "description": "Install only locked release tooling, without the project",
                    "default": "false",
                }
            },
        )
        self.assertEqual(
            setup["runs"],
            {
                "using": "composite",
                "steps": [
                    {
                        "uses": "pdm-project/setup-pdm@544d7237314ee09c256785bd360f6b30add38b37",
                        "with": {
                            "python-version": "3.14",
                            "version": "2.29.2",
                            "cache": "${{ inputs.release-only != 'true' }}",
                            "cache-restore-exact-match": True,
                        },
                    },
                    {
                        "if": "inputs.release-only != 'true'",
                        "run": "pdm install --check",
                        "shell": "bash",
                    },
                    {"if": "inputs.release-only == 'true'", "run": INSTALL, "shell": "bash"},
                ],
            },
        )

    def test_external_actions_are_approved_and_immutable(self):
        files = [*ROOT.glob(".github/workflows/*.yml"), *ROOT.glob(".github/actions/**/action.yml")]
        found = []
        for path in files:
            found.extend(re.findall(r"^\s*(?:- )?uses: (\S+)", path.read_text(), re.MULTILINE))
        self.assertTrue(found)
        for action in found:
            with self.subTest(action=action):
                if action.startswith(("./", "$/")):
                    continue
                self.assertRegex(action, r"^(?:actions|pdm-project)/[^@]+@[a-f0-9]{40}$")


class LockedReleaseCliTest(unittest.TestCase):
    def test_release_sync_selects_no_project_or_unrelated_groups(self):
        setup = yaml.safe_load((ROOT / ".github/actions/setup/action.yml").read_text())
        command = setup["runs"]["steps"][-1]["run"].splitlines()[-1].split()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("pyproject.toml", "pdm.lock"):
                shutil.copyfile(ROOT / name, root / name)
            environment = root / ".venv"
            venv.EnvBuilder().create(environment)
            result = subprocess.run(  # noqa: S603 - repository-owned dry-run installation command.
                [*command, "--dry-run"],
                cwd=root,
                env=os.environ
                | {
                    "PDM_PYTHON": str(environment / "bin/python"),
                    "PDM_IGNORE_ACTIVE_VENV": "1",
                    "PDM_CHECK_UPDATE": "false",
                },
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertEqual(
            set(re.findall(r"(?m)^\s+- ([\w-]+) ", output)),
            {
                package["name"]
                for package in tomllib.loads((ROOT / "pdm.lock").read_text())["package"]
                if "release" in package["groups"]
            },
        )

    def test_titles_preserve_release_decisions_and_outputs(self):
        for title, tag, released in (
            ("fix: repair behavior", "v0.23.2", "true"),
            ("feat: add behavior", "v0.24.0", "true"),
            ("docs: clarify behavior", "v0.23.1", "false"),
        ):
            with self.subTest(title=title), TemporaryDirectory() as directory:
                self.assert_release(Path(directory), title, tag, released)

    def assert_release(self, root, title, tag, released):
        clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment = clean | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        git(root, "init", "-q", "-b", "main", env=environment)
        git(
            root,
            "remote",
            "add",
            "origin",
            "https://github.com/example/release-test.git",
            env=environment,
        )
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["semantic_release"]
        config = tomli_w.dumps({"tool": {"semantic_release": project}})
        root.joinpath("pyproject.toml").write_text(config, encoding="utf-8")
        git(root, "add", "pyproject.toml", env=environment)
        identity = ("-c", "user.name=Setup role", "-c", "user.email=setup@example.invalid")
        git(root, *identity, "commit", "-qm", "chore: baseline", env=environment)
        git(root, "tag", "v0.23.1", env=environment)
        git(root, *identity, "commit", "--allow-empty", "-qm", title, env=environment)
        before = git(root, "rev-parse", "HEAD", env=environment)
        output = root / "output"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "semantic_release",
                "version",
                "--no-commit",
                "--tag",
                "--no-push",
                "--no-changelog",
                "--no-vcs-release",
                "--skip-build",
            ],
            cwd=root,
            env=environment | {"GITHUB_OUTPUT": str(output), "GITHUB_ACTIONS": "true"},
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        values = output.read_text(encoding="utf-8")
        self.assertIn(f"released={released}", values)
        self.assertIn(f"tag={tag}", values)
        self.assertEqual(git(root, "rev-parse", "HEAD", env=environment), before)
        self.assertEqual(root.joinpath("pyproject.toml").read_text(), config)
        if released == "true":
            self.assertEqual(git(root, "rev-parse", f"{tag}^{{commit}}", env=environment), before)
