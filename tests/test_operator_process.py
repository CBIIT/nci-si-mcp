"""Cancellation and deadlines terminate only the worker process group created for this job."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.operator_process import LIFELINE, run_owned
from scripts.operator_worker import clean_environment


class OperatorProcessTest(unittest.TestCase):
    def test_normal_worker_exit_stops_a_descendant_that_ignores_termination(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = directory / "child.json"
            child_code = (
                "import os,signal,time,json; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"open({str(marker)!r},'w').write(json.dumps(os.getpid())); time.sleep(300)"
            )
            leader_code = (
                "import subprocess,sys,time,pathlib; "
                f"subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
                f"marker=pathlib.Path({str(marker)!r}); "
                "\nwhile not marker.exists(): time.sleep(.01)"
            )
            result = run_owned(
                [sys.executable, "-c", leader_code],
                directory=directory,
                environment=clean_environment(directory),
                seconds=5,
                cancelled=threading.Event(),
            )
            pid = json.loads(marker.read_text())
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.01)
                else:
                    self.fail("Owned descendant survived normal worker completion")
            finally:
                with suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
        self.assertEqual(result["state"], "completed")

    def test_lost_parent_pipe_stops_the_owned_worker_without_a_pid_file(self):
        read_fd, write_fd = os.pipe()
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = directory / "ready"
            code = (
                "from scripts.operator_process import watch_parent; import time,signal; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"watch_parent(); open({str(marker)!r},'w').write('ready'); time.sleep(300)"
            )
            child = subprocess.Popen(  # noqa: S603 - owned lifeline test worker
                [sys.executable, "-c", code],
                pass_fds=(read_fd,),
                start_new_session=True,
                env=clean_environment(directory) | {LIFELINE: str(read_fd)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            os.close(read_fd)
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists())
                os.close(write_fd)
                write_fd = -1
                self.assertEqual(child.wait(timeout=5), -signal.SIGKILL)
            finally:
                if write_fd >= 0:
                    os.close(write_fd)
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)

    def test_deadline_reaps_owned_worker_and_does_not_claim_success(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = directory / "pid.json"
            code = (
                f"import os,json,time; open({str(marker)!r},'w').write(json.dumps(os.getpid())); "
                "time.sleep(300)"
            )
            result = run_owned(
                [sys.executable, "-c", code],
                directory=directory,
                environment=clean_environment(directory),
                seconds=1,
                cancelled=threading.Event(),
            )
            pid = json.loads(marker.read_text())
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["reason"], "deadline")
        self.assertNotEqual(result["exit_code"], 0)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_already_cancelled_job_starts_no_process(self):
        cancelled = threading.Event()
        cancelled.set()
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = directory / "started"
            result = run_owned(
                [sys.executable, "-c", f"open({str(marker)!r},'w').write('started')"],
                directory=directory,
                environment=clean_environment(directory),
                seconds=5,
                cancelled=cancelled,
            )
            self.assertFalse(marker.exists())
        self.assertEqual(result["state"], "cancelled")
        self.assertIsNone(result["exit_code"])

    def test_normal_completion_keeps_actual_nonzero_exit_without_a_retry(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = run_owned(
                [sys.executable, "-c", "raise SystemExit(7)"],
                directory=directory,
                environment=clean_environment(directory),
                seconds=5,
                cancelled=threading.Event(),
            )
        self.assertEqual(result, {"state": "failed", "exit_code": 7, "reason": "worker_failed"})
