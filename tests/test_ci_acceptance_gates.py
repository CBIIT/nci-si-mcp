"""Both fixture runs of the acceptance suite run its gates, neither is given a secret, and every
parallel run steals work."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from scripts import github_setup as module

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"
FIXTURE_STEP = "Run the suite against the fixtures"
# The permission gates (X-25 to X-28) run only with a security server; without one they are
# unrun, and an unrun gate leaves every tool INCOMPLETE (#265).
FIXTURE_ENV = {
    "NCI_SI_ACCEPTANCE_SECURITY_SERVER": "python ../scripts/permissions_fixture.py",
    "NCI_SI_ACCEPTANCE_PREPARE": 'nci-si-mcp index-sample $(cat "$NCI_SI_ACCEPTANCE_INDEX_CODES")',
}


LIVE_STEP = "Run the suite against the live services"


def jobs(workflow):
    return yaml.safe_load((WORKFLOWS / workflow).read_text())["jobs"]


def step(workflow, job, name):
    steps = jobs(workflow)[job]["steps"]
    (found,) = [step for step in steps if step.get("name") == name]
    return found


def fixture_env(workflow, job):
    return step(workflow, job, FIXTURE_STEP)["env"]


def suite_line(workflow, job, name):
    """The line of a step that runs the suite."""

    (line,) = [
        line.strip()
        for line in step(workflow, job, name)["run"].splitlines()
        if line.strip().startswith("pdm run acceptance ")
    ]
    return line


class AcceptanceGateWorkflowTest(unittest.TestCase):
    def test_live_placeholders_are_absent_and_real_values_never_enter_logs(self):
        live = step("acceptance-live.yml", "acceptance-live", LIVE_STEP)
        self.assertEqual(
            live["env"],
            {
                "NCI_SI_ACCEPTANCE_MODE": "live",
                "NCI_SI_ACCEPTANCE_PREPARE": FIXTURE_ENV["NCI_SI_ACCEPTANCE_PREPARE"],
                "NCI_SI_EVS_LICENSE_KEY": "${{ secrets.NCI_SI_EVS_LICENSE_KEY }}",
                "NCI_SI_CADSR_CREDENTIAL": "${{ secrets.NCI_SI_CADSR_CREDENTIAL }}",
                "CREDENTIAL_PLACEHOLDER": module.PLACEHOLDER,
            },
        )
        self.assertEqual(
            live,
            {
                "name": LIVE_STEP,
                "env": {
                    "NCI_SI_ACCEPTANCE_MODE": "live",
                    "NCI_SI_ACCEPTANCE_PREPARE": FIXTURE_ENV["NCI_SI_ACCEPTANCE_PREPARE"],
                    "NCI_SI_EVS_LICENSE_KEY": "${{ secrets.NCI_SI_EVS_LICENSE_KEY }}",
                    "NCI_SI_CADSR_CREDENTIAL": "${{ secrets.NCI_SI_CADSR_CREDENTIAL }}",
                    "CREDENTIAL_PLACEHOLDER": "NCI_SI_CONFIGURE_ME",
                },
                "run": 'absent=""\n'
                "for name in NCI_SI_EVS_LICENSE_KEY NCI_SI_CADSR_CREDENTIAL; do\n"
                '  if [ -z "${!name}" ] || [ "${!name}" = "$CREDENTIAL_PLACEHOLDER" ]; then\n'
                '    absent="$absent $name"\n    unset "$name"\n  fi\ndone\n'
                'echo "- Credentials absent from the live run:${absent:- none}" '
                '| tee -a acceptance/network.md >> "$GITHUB_STEP_SUMMARY"\n'
                "status=0\npdm run acceptance -n 4 --dist worksteal "
                "--report=live.json || status=$?\n"
                'if [ "$status" -gt 1 ] || [ ! -s acceptance/live.json ]; then\n'
                '  echo "::error title=The live run did not finish::'
                'exit status ${status}, or no report."\n'
                "  exit 1\nfi\n"
                "pdm run python -m nci_si_acceptance.report acceptance/live.json "
                "--check-complete live\n",
            },
        )
        for supplied in (
            ("", ""),
            (module.PLACEHOLDER, module.PLACEHOLDER),
            ("PRIVATE-LIVE-EVS-CANARY", "PRIVATE-LIVE-CADSR-CANARY"),
            (module.PLACEHOLDER, "PRIVATE-LIVE-CADSR-CANARY"),
            ("PRIVATE-LIVE-EVS-CANARY", module.PLACEHOLDER),
        ):
            with self.subTest(supplied=supplied), TemporaryDirectory() as directory:
                self.assert_live_environment(Path(directory), live, supplied)

    def assert_live_environment(self, root, live, supplied):
        root.joinpath("acceptance").mkdir()
        executable = root / "pdm"
        executable.write_text(
            f"#!{sys.executable}\n"
            + """
import json, os, sys
from pathlib import Path
if sys.argv[1:3] == ["run", "acceptance"]:
    Path("seen.json").write_text(json.dumps({k:v for k,v in os.environ.items() if k in
        ("NCI_SI_EVS_LICENSE_KEY", "NCI_SI_CADSR_CREDENTIAL")}))
    Path("acceptance/live.json").write_text("{}")
"""
        )
        executable.chmod(0o700)
        environment = os.environ | {
            "PATH": str(root) + os.pathsep + os.environ["PATH"],
            "GITHUB_STEP_SUMMARY": str(root / "summary"),
            "CREDENTIAL_PLACEHOLDER": live["env"]["CREDENTIAL_PLACEHOLDER"],
            "NCI_SI_EVS_LICENSE_KEY": supplied[0],
            "NCI_SI_CADSR_CREDENTIAL": supplied[1],
        }
        result = subprocess.run(  # noqa: S603 - repository step with an owned fake acceptance CLI.
            ["bash", "-c", live["run"]],  # noqa: S607
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        names = ("NCI_SI_EVS_LICENSE_KEY", "NCI_SI_CADSR_CREDENTIAL")
        expected = {
            name: value
            for name, value in zip(names, supplied, strict=True)
            if value not in ("", module.PLACEHOLDER)
        }
        self.assertEqual(json.loads(root.joinpath("seen.json").read_text()), expected)
        summary = root.joinpath("summary").read_text()
        absent = "".join(f" {name}" for name in names if name not in expected) or " none"
        self.assertEqual(summary, f"- Credentials absent from the live run:{absent}\n")
        self.assertNotIn("PRIVATE-LIVE-", result.stdout + result.stderr + summary)
        self.assertEqual(root.joinpath("acceptance/network.md").read_text(), summary)

    def test_the_weekly_fixture_run_runs_the_same_gates_as_ci(self):
        self.assertEqual(fixture_env("ci.yml", "acceptance"), FIXTURE_ENV)
        self.assertEqual(fixture_env("acceptance-live.yml", "acceptance-live"), FIXTURE_ENV)

    def test_no_secret_reaches_either_fixture_run(self):
        # The job's environment and the fixture step's environment, inputs, condition and
        # script: everything the fixture run can see.
        for workflow, job in (("ci.yml", "acceptance"), ("acceptance-live.yml", "acceptance-live")):
            with self.subTest(workflow=workflow):
                fixture = step(workflow, job, FIXTURE_STEP)
                visible = {
                    "job env": jobs(workflow)[job].get("env", {}),
                    **{key: fixture.get(key, "") for key in ("env", "with", "if", "run")},
                }
                leaking = [key for key, part in visible.items() if "secrets." in json.dumps(part)]
                self.assertEqual(leaking, [])

    def test_every_parallel_suite_run_steals_work(self):
        # The default load distribution left one worker with the slow tail (#222).
        fixture = "pdm run acceptance -n 4 --dist worksteal --report=fixture.json || status=$?"
        live = "pdm run acceptance -n 4 --dist worksteal --report=live.json || status=$?"
        runs = {
            ("ci.yml", "acceptance", FIXTURE_STEP): fixture,
            ("acceptance-live.yml", "acceptance-live", FIXTURE_STEP): fixture,
            ("acceptance-live.yml", "acceptance-live", LIVE_STEP): live,
        }
        for (workflow, job, name), expected in runs.items():
            with self.subTest(workflow=workflow, step=name):
                self.assertEqual(suite_line(workflow, job, name), expected)
