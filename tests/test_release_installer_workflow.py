import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
DOCUMENT = ROOT / "docs" / "installer-targets.md"
JOB_NAME = "local installer (macOS/Linux)"


def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def job():
    return workflow()["jobs"]["local-installer"]


def named_steps():
    return [step for step in job()["steps"] if "name" in step]


def documented_commands():
    text = DOCUMENT.read_text(encoding="utf-8")
    blocks = re.findall(r"^```(?:bash|sh|shell)\n(.*?)^```", text, re.DOTALL | re.MULTILINE)
    return {line.strip() for block in blocks for line in block.splitlines() if line.strip()}


class ReleaseInstallerWorkflowTest(unittest.TestCase):
    def test_job_runs_only_for_the_exact_release_with_read_only_permissions(self):
        value = job()
        self.assertEqual(value["name"], JOB_NAME + " (${{ matrix.os }})")
        self.assertEqual(value["needs"], "release")
        self.assertEqual(value["if"], "needs.release.outputs.image_tag != ''")
        self.assertEqual(value["permissions"], {"contents": "read"})
        self.assertEqual(value["runs-on"], "${{ matrix.os }}")
        self.assertEqual(
            value["strategy"],
            {"fail-fast": False, "matrix": {"os": ["macos-latest", "ubuntu-24.04"]}},
        )
        self.assertEqual(value["timeout-minutes"], 20)

    def test_checkout_is_the_exact_release_without_credentials(self):
        checkout = job()["steps"][0]
        self.assertEqual(
            checkout,
            {
                "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                "with": {
                    "ref": "${{ needs.release.outputs.image_sha }}",
                    "fetch-depth": 0,
                    "persist-credentials": False,
                },
            },
        )

    def test_installer_commands_run_outside_checkout_with_the_release_executable(self):
        steps = {step["name"]: step for step in named_steps()}
        install = steps["Install the released installer"]
        self.assertEqual(install["working-directory"], "${{ runner.temp }}")
        self.assertIn("@${RELEASE_TAG}", install["run"])
        self.assertIn('"$PYTHON" -m venv "$INSTALLER_VENV"', documented_commands())
        self.assertIn('"$PYTHON" -m venv "$INSTALLER_VENV"', install["run"])
        lifecycle = steps["Verify stdio and HTTP installer lifecycles"]
        self.assertEqual(lifecycle["working-directory"], "${{ runner.temp }}")
        lines = lifecycle["run"].splitlines()
        installer = '"$INSTALLER_VENV/bin/nci-si-install"'
        for command in ("check", "run", "status", "start", "stop", "uninstall"):
            self.assertTrue(any(line.startswith(f"{installer} {command} ") for line in lines))
        self.assertNotIn("pdm run nci-si-install", lifecycle["run"])

    def test_acceptance_targets_only_release_resolution_and_no_index_search(self):
        run = next(
            step["run"]
            for step in named_steps()
            if step["name"] == "Verify stdio and HTTP installer lifecycles"
        )
        self.assertIn("test_a_resolved_release_is_never_cached", run)
        self.assertIn("test_an_index_mode_without_an_index_is_unavailable", run)
        self.assertIn("NCI_SI_ACCEPTANCE_SERVER", run)
        self.assertIn("NCI_SI_ACCEPTANCE_URL", run)
        self.assertIn("acceptance/expected/fixture.json", run)

    def test_uninstall_check_distinguishes_owned_and_unowned_paths(self):
        run = next(
            step["run"]
            for step in named_steps()
            if step["name"] == "Verify stdio and HTTP installer lifecycles"
        )
        self.assertIn('test -f "$STDIO_ROOT/unowned"', run)
        self.assertIn('test ! -e "$STDIO_ROOT/venv"', run)
        self.assertIn('test ! -e "$STDIO_ROOT/installer-state.json"', run)

    def test_every_operator_command_is_documented(self):
        documented = documented_commands()
        run = next(
            step["run"]
            for step in named_steps()
            if step["name"] == "Verify stdio and HTTP installer lifecycles"
        )
        operator_lines = [line.strip() for line in run.splitlines() if "nci-si-install" in line]
        self.assertEqual([line for line in operator_lines if line not in documented], [])
