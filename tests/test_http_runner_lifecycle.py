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
