"""Owned-process inspection and waits enforce their independent boundary contracts."""

import os
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts.validation import mutation_run as runner


class InspectionContractTest(unittest.TestCase):
    def test_numeric_identities_require_unsigned_ascii_decimal_in_every_column(self):
        # Given ps identity text, parsing accepts decimal zero but refuses int() extensions.
        for linux in (False, True):
            fields = ["123", "99", "123", *(["123"] if linux else []), "0", "502"]
            self.assertEqual(runner._member(" ".join([*fields, "Z"]), linux=linux).uid, 0)
            for column in range(len(fields)):
                for invalid in ("+123", "-123", "1_23", "\u0661\u0662\u0663", "\uff11\uff12\uff13"):
                    with self.subTest(linux=linux, column=column, invalid=invalid):
                        row = fields.copy()
                        row[column] = invalid
                        with self.assertRaisesRegex(ValueError, "Invalid.*identity"):
                            runner._member(" ".join([*row, "Z"]), linux=linux)

    def test_real_ps_overrides_hostile_environment_and_captures_parseable_owned_zombie(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        execute, evidence = subprocess.run, []

        def inspect(command, **kwargs):
            result = execute(command, **kwargs)
            evidence.append((command, kwargs["env"], result.stdout, result.stderr))
            return result

        try:
            self.assertEqual(runner._wait_exit(child.pid, runner.monotonic() + 5), 0)
            with (
                patch.dict(os.environ, {"LC_ALL": "invalid_LOCALE", "COMMAND_MODE": "legacy"}),
                patch.object(runner.subprocess, "run", side_effect=inspect),
            ):
                self.assertTrue(
                    runner._terminal_group(
                        runner._Owner(child.pid, os.geteuid(), os.getuid()), runner.monotonic() + 5
                    )
                )
            command, env, stdout, stderr = evidence[0]
            linux = sys.platform.startswith("linux")
            self.assertEqual(
                command,
                [
                    "/bin/ps",
                    "-s" if linux else "-g",
                    str(child.pid),
                    "-o",
                    "pid=,ppid=,pgid=," + ("sid=," if linux else "") + "uid=,ruid=,state=",
                ],
            )
            self.assertEqual((env["LC_ALL"], env["COMMAND_MODE"]), ("C", "unix2003"))
            self.assertIsInstance(stdout, str)
            self.assertTrue(stdout.endswith("\n"))
            self.assertEqual(stderr, "")
        finally:
            child.wait(timeout=5)

    def test_linux_nonzero_ps_with_plausible_output_is_unavailable(self):
        def failed_ps(command, *, check, **_kwargs):
            result = subprocess.CompletedProcess(command, 1, "123 99 123 123 501 502 Z\n", "")
            if check:
                raise subprocess.CalledProcessError(1, command, output=result.stdout)
            return result

        with (
            patch.object(runner.sys, "platform", "linux"),
            patch.object(runner.subprocess, "run", side_effect=failed_ps),
            self.assertRaises(subprocess.CalledProcessError) as raised,
        ):
            runner._group_snapshot(runner._Owner(123, 501, 502), runner.monotonic() + 5)
        self.assertEqual(raised.exception.returncode, 1)
        self.assertEqual(raised.exception.stdout, "123 99 123 123 501 502 Z\n")

    def test_ps_timeout_is_the_subsecond_remaining_budget(self):
        timeouts = []

        def inspect(command, *, timeout, **_kwargs):
            timeouts.append(timeout)
            return subprocess.CompletedProcess(command, 0, "123 99 123 501 502 Z\n", "")

        with (
            patch.object(runner.sys, "platform", "darwin"),
            patch.object(runner, "monotonic", side_effect=(5.75, 5.875)),
            patch.object(runner.subprocess, "run", side_effect=inspect),
        ):
            rows = runner._group_snapshot(runner._Owner(123, 501, 502), 6)
        self.assertEqual(timeouts, [0.25])
        self.assertEqual([(row.pid, row.state) for row in rows], [(123, "Z")])

    def test_wait_and_both_grace_paths_yield_until_their_original_deadline(self):
        # Time advances only on sleep; a finite work guard fails busy loops without hanging.
        for mode in ("execution", "grace", "failed inspection"):
            with self.subTest(mode=mode):
                now, work, slept, killed = [0.0], [], [], []
                failure = ValueError("inspection unavailable")

                def clock(now=now, work=work):
                    work.append(None)
                    self.assertLessEqual(len(work), 100, "busy wait without yielding")
                    return now[0]

                def pause(seconds, now=now, slept=slept):
                    self.assertGreater(seconds, 0)
                    slept.append(seconds)
                    now[0] += seconds

                with (
                    patch.object(runner, "monotonic", side_effect=clock),
                    patch.object(runner, "sleep", side_effect=pause),
                    patch.object(runner, "_observe", return_value=None),
                    patch.object(
                        runner,
                        "_terminal_group",
                        return_value=False,
                        side_effect=failure if mode == "failed inspection" else None,
                    ),
                    patch.object(runner, "_signal_owned", return_value=True),
                    patch.object(
                        runner,
                        "_kill_owned",
                        side_effect=lambda *_, now=now, killed=killed: killed.append(now[0]),
                    ),
                ):
                    if mode == "execution":
                        self.assertIsNone(runner._wait_exit(123, 0.025))
                    elif mode == "grace":
                        self.assertFalse(
                            runner._grace_complete(runner._Owner(123, 501, 502), 1, 0.025)
                        )
                    else:
                        with self.assertRaises(ValueError) as raised:
                            runner._cleanup_group(runner._Owner(123, 501, 502), 1, 0.025)
                        self.assertIs(raised.exception, failure)
                        self.assertEqual(killed, [0.025])
                self.assertEqual(now[0], 0.025)
                self.assertGreaterEqual(len(slept), 2)
                self.assertLessEqual(len(slept), 4)
                self.assertLessEqual(len(work), 15)

    def test_successful_term_and_terminal_snapshot_reap_without_final_kill(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True
        )
        send = runner._signal_owned

        def terminate(owner, sig, deadline):
            if sig == signal.SIGKILL:
                self.fail("terminal TERM grace attempted unnecessary final KILL")
            delivered = send(owner, sig, deadline)
            self.assertEqual(runner._wait_exit(child.pid, deadline), -signal.SIGTERM)
            return delivered

        try:
            with patch.object(runner, "_signal_owned", side_effect=terminate):
                runner._finish_owned(child, runner._Owner(child.pid, os.geteuid(), os.getuid()))
            self.assertEqual(child.returncode, -signal.SIGTERM)
            with self.assertRaises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
