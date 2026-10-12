import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Reaches Settings.from_env() without setting anything itself.
AFFECTED = (
    "tests/test_release_selection.py::ReleaseSelectionTest::"
    "test_real_stateless_http_resolves_each_call"
)


class DeveloperEnvironmentTest(unittest.TestCase):
    def test_a_developers_settings_do_not_decide_a_unit_tests_result(self):
        # Given a shell that selects the fixture upstream mode, as a developer's may
        environment = {**os.environ, "NCI_SI_UPSTREAM_MODE": "fixture"}
        # When one unit test that builds its settings from the environment runs in a child
        run = subprocess.run(  # noqa: S603 - fixed executable and a repository test id
            [sys.executable, "-m", "pytest", AFFECTED, "-q", "-p", "no:cacheprovider", "--no-cov"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        # Then it passes exactly as it does in a clean shell
        self.assertEqual(run.returncode, 0, run.stdout[-1500:])
