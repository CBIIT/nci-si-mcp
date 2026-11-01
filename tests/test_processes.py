"""The shared process helpers reap what they own and bound every wait."""

import contextlib
import io
import select
import signal
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.processes import SERVER_LOG_TAIL_BYTES, print_server_log_tail, stop, wait_until

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


class SlowToExit:
    """A stand-in process that ignores SIGTERM, finishes with code 7 once the wait given to it
    is at least `grace` seconds, and dies of SIGKILL otherwise."""

    def __init__(self, grace: float) -> None:
        self.grace = grace
        self.returncode = None
        self.killed = False
        self.waits = []

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.killed = sig == signal.SIGKILL

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.killed:
            self.returncode = -signal.SIGKILL
        elif timeout >= self.grace:
            self.returncode = 7
        else:
            raise subprocess.TimeoutExpired("slow", timeout)
        return self.returncode


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
        # Exits by itself, but only if it is allowed to wait at least ten seconds after SIGTERM.
        process = SlowToExit(grace=10)
        stop(process)
        self.assertEqual(process.returncode, 7)

    def test_a_child_outliving_its_grace_period_is_waited_for_ten_seconds_twice(self):
        process = SlowToExit(grace=11)
        stop(process)
        self.assertEqual(process.waits, [10, 10])
        self.assertEqual(process.returncode, -signal.SIGKILL)

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
        with patch("scripts.processes.time.monotonic", side_effect=(0, 0, 10.5)):
            self.assertFalse(wait_until(lambda: False, 10, interval=0))

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

    def test_a_second_sigterm_during_cleanup_does_not_interrupt_unwinding(self):
        script = (
            "import sys,time; from scripts.processes import exit_on_sigterm; exit_on_sigterm(); "
            "print('ready',flush=True)\n"
            "try:\n    time.sleep(300)\n"
            "except KeyboardInterrupt:\n"
            "    print('cleaning',flush=True)\n"
            "    sys.stdin.readline()\n"
            "    print('cleaned',flush=True)\n"
        )
        child = subprocess.Popen(  # noqa: S603 - fixed test child, reaped below
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).parents[1],
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "ready")
            child.send_signal(signal.SIGTERM)
            self.assertEqual(child.stdout.readline().strip(), "cleaning")
            child.send_signal(signal.SIGTERM)
            child.stdin.write("finish\n")
            child.stdin.flush()
            self.assertEqual(child.wait(timeout=10), 0)
            self.assertEqual(child.stdout.readline().strip(), "cleaned")
            self.assertEqual(child.stderr.read(), "")
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            child.stdin.close()
            child.stdout.close()
            child.stderr.close()


class PrintServerLogTailTest(unittest.TestCase):
    def tail(self, log: Path) -> str:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            print_server_log_tail(log)
        return stderr.getvalue()

    def test_a_log_that_was_never_written_prints_nothing(self):
        with TemporaryDirectory() as directory:
            self.assertEqual(self.tail(Path(directory) / "server.log"), "")

    def test_the_tail_is_the_last_bytes_and_undecodable_bytes_are_replaced(self):
        with TemporaryDirectory() as directory:
            log = Path(directory) / "server.log"
            log.write_bytes(b"early" + b"a" * SERVER_LOG_TAIL_BYTES + b"\xff end")
            self.assertEqual(
                self.tail(log),
                f"Owned server log, last {SERVER_LOG_TAIL_BYTES} bytes:\n"
                + "a" * (SERVER_LOG_TAIL_BYTES - 5)
                + "� end",
            )
