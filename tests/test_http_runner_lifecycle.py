"""The HTTP acceptance gate cannot reuse stale evidence or leave an owned child alive."""

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from scripts import acceptance_http as runner


class HTTPRunnerLifecycleTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.report = self.directory / "report.json"
        self.valid = {
            "mode": "fixture",
            "run": {"exit_status": 0, "selected": 5, "finished": 5, "worker_crashes": 0},
            "transport": "streamable-http",
            "failed_gates": [],
            "tests": {name: {"outcome": "skipped"} for name in runner.UNPREPARED}
            | {"required-case": {"outcome": "passed"}},
        }
        expected = self.directory / "expected"
        expected.mkdir()
        (expected / "fixture.json").write_text(
            json.dumps(dict.fromkeys(self.valid["tests"], "passed"))
        )
        for name, value in (("REPORT", self.report), ("ACCEPTANCE", self.directory)):
            replacement = patch.object(runner, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def run_with(self, report, exit_code):
        def wait():
            if report is not None:
                self.report.write_text(json.dumps(report))
            return exit_code

        process = Mock(wait=Mock(side_effect=wait), poll=Mock(return_value=exit_code))
        with (
            patch.object(runner.subprocess, "Popen", return_value=process),
            redirect_stdout(io.StringIO()),
        ):
            return runner.run_suite(8000, self.directory / "control")

    def test_old_success_is_removed_before_a_run_that_writes_no_report(self):
        self.report.write_text(json.dumps(self.valid))
        self.assertEqual(self.run_with(None, 0), 1)
        self.assertFalse(self.report.exists())

    def test_a_complete_report_cannot_override_a_failed_process(self):
        self.assertEqual(self.run_with(self.valid, 1), 1)
        self.assertEqual(json.loads(self.report.read_text()), self.valid)

    def test_zero_exit_still_requires_the_complete_remote_verdict(self):
        incomplete = self.valid | {"tests": {"required-case": {"outcome": "passed"}}}
        self.assertEqual(self.run_with(incomplete, 0), 1)
        self.assertEqual(self.run_with(self.valid, 0), 0)

    def test_zero_exit_cannot_override_an_incomplete_report_or_a_worker_crash(self):
        for run in (
            {"exit_status": 0, "selected": 6, "finished": 5, "worker_crashes": 0},
            {"exit_status": 0, "selected": 5, "finished": 5, "worker_crashes": 1},
        ):
            with self.subTest(run=run):
                self.assertEqual(self.run_with(self.valid | {"run": run}, 0), 1)

    def test_operator_report_and_environment_are_isolated_from_default_ci_output(self):
        private = self.directory / "operator.json"
        self.report.write_text("Keep existing engineering result")
        observed = []

        def launch(command, **options):
            observed.append((command, options["env"]))
            private.write_text(json.dumps(self.valid))
            return Mock(wait=Mock(return_value=0), poll=Mock(return_value=0))

        with (
            patch.object(runner.subprocess, "Popen", side_effect=launch),
            redirect_stdout(io.StringIO()),
        ):
            result = runner.run_suite(
                8000,
                self.directory / "control",
                report=private,
                environment={"PATH": "controlled-path", "HOME": "isolated-home"},
            )
        self.assertEqual(result, 0)
        self.assertEqual(self.report.read_text(), "Keep existing engineering result")
        command, environment = observed[0]
        self.assertIn("--report=" + str(private), command)
        self.assertEqual(environment["HOME"], "isolated-home")
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", environment)

    def test_cancellation_reaps_the_owned_suite_process_before_propagating(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        wait = child.wait

        def interrupted_wait(*, timeout=None):
            if timeout is None:
                raise KeyboardInterrupt
            return wait(timeout=timeout)

        try:
            with (
                patch.object(runner.subprocess, "Popen", return_value=child),
                patch.object(child, "wait", side_effect=interrupted_wait),
                self.assertRaises(KeyboardInterrupt),
            ):
                runner.run_suite(8000, self.directory / "control")
            self.assertIsNotNone(child.poll())
            self.assertFalse(self.report.exists())
        finally:
            if child.poll() is None:
                child.kill()
            wait(timeout=5)
