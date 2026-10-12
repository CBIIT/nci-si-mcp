import io
import unittest
from contextlib import redirect_stdout
from copy import deepcopy

from scripts.acceptance_http import UNPREPARED, differences, report_skips


class HTTPVerdictTest(unittest.TestCase):
    def report(self):
        return {
            "transport": "streamable-http",
            "failed_gates": [],
            "tests": {name: {"outcome": "skipped"} for name in UNPREPARED}
            | {"applicable-case": {"outcome": "passed"}},
        }

    def test_only_the_complete_remote_run_with_required_skips_passes(self):
        report = self.report()
        output = io.StringIO()
        with redirect_stdout(output):
            report_skips(report["tests"])
        self.assertEqual(differences(report, set(report["tests"])), [])
        self.assertIn("1 executed, 4 skipped", output.getvalue())
        for name in UNPREPARED:
            self.assertIn(name, output.getvalue())

    def test_a_failure_unexpected_skip_or_unimplemented_case_cannot_pass(self):
        report = self.report()
        for outcome in ("failed", "error", "skipped", "not_implemented"):
            with self.subTest(outcome=outcome):
                report["tests"]["applicable-case"]["outcome"] = outcome
                self.assertNotEqual(differences(report, set(report["tests"])), [])

    def test_missing_cases_wrong_transport_and_failed_gates_cannot_pass(self):
        original = self.report()
        for changed in (
            {"tests": {name: row for name, row in original["tests"].items() if name in UNPREPARED}},
            {"transport": "stdio"},
            {"failed_gates": ["inventory"]},
        ):
            with self.subTest(changed=changed):
                changed_report = deepcopy(original) | changed
                self.assertNotEqual(differences(changed_report, set(original["tests"])), [])
