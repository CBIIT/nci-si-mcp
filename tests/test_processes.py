"""The shared process helpers reap what they own and bound every wait."""

import select
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.processes import stop, wait_until

IGNORES_TERMINATION = (
    "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
    "print('ready',flush=True); time.sleep(300)"
)


def spawn(script: str, **options) -> subprocess.Popen[str]:
    process = subprocess.Popen(  # noqa: S603 - fixed test child, reaped by the caller
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True, **options
    )
    if not select.select([process.stdout], [], [], 5)[0]:
        process.kill()
        raise TimeoutError("Test child did not start")
    process.stdout.readline()
    return process


class StopTest(unittest.TestCase):
    def test_a_child_that_ignores_termination_is_killed_and_reaped(self):
        process = spawn(IGNORES_TERMINATION)
        wait = process.wait
        try:
            with patch.object(process, "wait", side_effect=lambda timeout: wait(timeout=0.1)):
                stop(process)
            self.assertEqual(process.returncode, -signal.SIGKILL)
        finally:
            if process.poll() is None:
                process.kill()
            wait(timeout=5)
            process.stdout.close()

    def test_a_slow_child_gets_its_full_grace_period_before_it_is_killed(self):
        # Ignores termination for 2 s, then exits by itself: the 10 s grace must outlast it.
        slow = (
            "import signal,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "print('ready',flush=True); time.sleep(2); sys.exit(7)"
        )
        process = spawn(slow)
        try:
            stop(process)
            self.assertEqual(process.returncode, 7)
        finally:
            process.stdout.close()

    def test_a_cooperative_child_is_terminated(self):
        process = spawn("import time; print('ready',flush=True); time.sleep(300)")
        try:
            stop(process)
            self.assertEqual(process.returncode, -signal.SIGTERM)
        finally:
            process.stdout.close()

    def test_a_finished_child_is_left_alone(self):
        process = spawn("print('ready',flush=True)")
        process.wait(timeout=5)
        try:
            stop(process)
            self.assertEqual(process.returncode, 0)
        finally:
            process.stdout.close()

    def test_group_stop_kills_a_descendant_that_outlives_its_leader(self):
        grandchild = (
            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "print('ready',flush=True); time.sleep(300)"
        )
        script = (
            "import subprocess,sys; "
            f"child = subprocess.Popen([sys.executable, '-c', {grandchild!r}], "
            "stdout=subprocess.PIPE, text=True); child.stdout.readline(); "
            "print(child.pid, flush=True)"
        )
        leader = subprocess.Popen(  # noqa: S603 - fixed test leader, reaped by stop
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            descendant = int(leader.stdout.readline())
            leader.wait(timeout=5)
            stop(leader, group=True)
            self.assertTrue(
                wait_until(lambda: not _alive(descendant), 5, interval=0.05),
                "The descendant survived the group stop",
            )
        finally:
            leader.stdout.close()


def _alive(pid: int) -> bool:
    try:
        # Reaped by init once its parent is gone; a zombie still answers signal 0.
        return subprocess.run(  # noqa: S603 - fixed argv
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        ).stdout.strip() not in ("", "Z")
    except OSError:
        return False


class WaitUntilTest(unittest.TestCase):
    def test_true_as_soon_as_the_predicate_holds(self):
        answers = iter((False, False, True))
        self.assertTrue(wait_until(lambda: next(answers), 5, interval=0))
        self.assertEqual(list(answers), [])

    def test_false_when_the_deadline_passes_first(self):
        with patch("scripts.processes.time.monotonic", side_effect=(0, 0, 11)):
            self.assertFalse(wait_until(lambda: False, 10, interval=0))

    def test_a_never_true_predicate_returns_false_after_about_the_given_seconds(self):
        started = time.monotonic()
        self.assertFalse(wait_until(lambda: False, 0.05, interval=0.01))
        self.assertLess(time.monotonic() - started, 0.5)

    def test_a_predicate_true_at_once_is_not_delayed_by_a_spent_deadline(self):
        self.assertTrue(wait_until(lambda: True, 0, interval=0))

    def test_a_predicate_error_propagates(self):
        def broken():
            raise OSError("probe failed")

        with self.assertRaisesRegex(OSError, "probe failed"):
            wait_until(broken, 5, interval=0)


class ExitOnSigtermTest(unittest.TestCase):
    def test_sigterm_raises_keyboard_interrupt(self):
        with TemporaryDirectory() as directory:
            marker = Path(directory) / "cleaned"
            script = (
                "import sys,time; from pathlib import Path; "
                "from scripts.processes import exit_on_sigterm; exit_on_sigterm(); "
                "print('ready',flush=True)\n"
                "try:\n    time.sleep(300)\n"
                "except KeyboardInterrupt:\n    Path(sys.argv[1]).write_text('cleaned')\n"
            )
            child = subprocess.Popen(  # noqa: S603 - fixed test child
                [sys.executable, "-c", script, str(marker)],
                stdout=subprocess.PIPE,
                text=True,
                cwd=Path(__file__).parents[1],
            )
            try:
                child.stdout.readline()
                child.send_signal(signal.SIGTERM)
                self.assertEqual(child.wait(timeout=10), 0)
            finally:
                child.stdout.close()
            self.assertEqual(marker.read_text(), "cleaned")
