"""Both fixture runs of the acceptance suite run its gates, neither is given a secret, and every
parallel run steals work."""

import json
import unittest
from pathlib import Path

import yaml

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
    def test_ci_runs_all_selftests_including_both_registry_modes(self):
        job = jobs("ci.yml")["selftest"]
        self.assertEqual(
            job["strategy"], {"fail-fast": False, "matrix": {"shard": ["1/3", "2/3", "3/3"]}}
        )
        self.assertEqual(job.get("env", {}), {})
        self.assertEqual(job.get("if", ""), "")
        self.assertEqual(job.get("needs", []), [])
        (run,) = [entry for entry in job["steps"] if "run" in entry]
        self.assertEqual(
            run,
            {
                "run": "pdm run acceptance-selftest --cov-fail-under=0",
                "env": {"SELFTEST_SHARD": "${{ matrix.shard }}"},
            },
        )

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
