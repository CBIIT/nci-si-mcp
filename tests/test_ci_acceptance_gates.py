"""Both fixture runs of the acceptance suite run its gates, and neither is given a secret."""

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


def fixture_env(workflow, job):
    steps = yaml.safe_load((WORKFLOWS / workflow).read_text())["jobs"][job]["steps"]
    (step,) = [step for step in steps if step.get("name") == FIXTURE_STEP]
    return step["env"]


class AcceptanceGateWorkflowTest(unittest.TestCase):
    def test_the_weekly_fixture_run_runs_the_same_gates_as_ci(self):
        self.assertEqual(fixture_env("ci.yml", "acceptance"), FIXTURE_ENV)
        self.assertEqual(fixture_env("acceptance-live.yml", "acceptance-live"), FIXTURE_ENV)

    def test_no_secret_reaches_either_fixture_run(self):
        for workflow, job in (("ci.yml", "acceptance"), ("acceptance-live.yml", "acceptance-live")):
            with self.subTest(workflow=workflow):
                values = fixture_env(workflow, job).values()
                self.assertEqual([value for value in values if "secrets." in value], [])
