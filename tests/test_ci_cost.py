"""The per-PR CI keeps its cost: the same events start it, and every job runs on Linux.

The macOS walk lives in its own workflow; the jobs' dependencies and conditions are owned by
the tests of the image selection and the badge isolation.
"""

import unittest
from pathlib import Path

import yaml

CI = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))


class CiCostTest(unittest.TestCase):
    def test_ci_starts_on_the_same_events(self):
        self.assertEqual(CI[True], {"push": {"branches": ["main"]}, "pull_request": None})

    def test_every_ci_job_runs_on_linux(self):
        runners = {job.get("runs-on") for job in CI["jobs"].values()}
        self.assertEqual(runners, {"ubuntu-latest"})
