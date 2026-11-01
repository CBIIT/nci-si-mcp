"""Owned cleanup retains failures and refuses invalid inspection or signal work."""

import os
import signal
import subprocess
import sys
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.processes import wait_until
from scripts.validation import mutation_run as runner


class OwnedFailureContractTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.children: list[subprocess.Popen[bytes]] = []
        self.addCleanup(self._reap_children)

    def _ready_child(self) -> subprocess.Popen[bytes]:
        ready = self.root / f"ready-{len(self.children)}"
        script = (
            f"import signal; from pathlib import Path; Path({str(ready)!r}).touch(); signal.pause()"
        )
        child = subprocess.Popen(  # noqa: S603 - authored child in its own owned session
            [sys.executable, "-c", script], start_new_session=True
        )
        self.children.append(child)
        self.assertTrue(wait_until(ready.exists, 5, interval=0.01))
        self.assertEqual((os.getpgid(child.pid), os.getsid(child.pid)), (child.pid, child.pid))
        return child

    def _reap_children(self) -> None:
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)

    @contextmanager
    def _owned_signals(self, pid: int) -> Iterator[list[tuple[int, signal.Signals]]]:
        delivered: list[tuple[int, signal.Signals]] = []
        killpg = os.killpg

        def deliver(group: int, sig: signal.Signals) -> None:
            self.assertEqual(group, pid, "cleanup must never signal an unowned group")
            killpg(group, sig)
            delivered.append((group, sig))

        with patch.object(runner.os, "killpg", side_effect=deliver):
            yield delivered

    def _assert_reaped(self, child: subprocess.Popen[bytes], sig: signal.Signals) -> None:
        self.assertEqual(child.returncode, -sig)
        with self.assertRaises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)

    def _finish_failure(
        self, reap_error: BaseException, cleanup_error: BaseException | None
    ) -> BaseException:
        child, sentinel = self._ready_child(), self._ready_child()
        owner = runner._Owner(child.pid, os.geteuid(), os.getuid())
        cleanup, wait = runner._cleanup_group, child.wait

        def cleanup_then_fail(owned: runner._Owner, deadline: float, grace: float) -> None:
            cleanup(owned, deadline, grace)
            if cleanup_error is not None:
                raise cleanup_error

        def reap_then_fail(timeout: float | None = None) -> int:
            wait(timeout=timeout)
            raise reap_error

        with (
            self._owned_signals(child.pid) as delivered,
            patch.object(runner, "_cleanup_group", side_effect=cleanup_then_fail),
            patch.object(child, "wait", side_effect=reap_then_fail),
            self.assertRaises(BaseException) as raised,
        ):
            runner._finish_owned(child, owner)
        self._assert_reaped(child, signal.SIGTERM)
        self.assertEqual(delivered, [(child.pid, signal.SIGTERM)])
        self.assertIsNone(sentinel.poll())
        return raised.exception

    def test_reap_failure_is_returned_unchanged_after_real_owned_cleanup(self) -> None:
        # Given real owned cleanup succeeds, when parent wait fails after reaping,
        # then the caller receives that exact failure and the unrelated child lives.
        reap_error = subprocess.TimeoutExpired("owned parent wait", 0)
        self.assertIs(self._finish_failure(reap_error, None), reap_error)

    def test_cleanup_and_reap_failures_both_survive_with_exact_identity(self) -> None:
        # Given cleanup stops the real child then reports failure, when parent wait
        # also fails after real reap, then both original failures remain diagnostic.
        cleanup_error = OSError("owned cleanup failed after stopping child")
        reap_error = KeyboardInterrupt("actual-parent wait interrupted after reap")
        failure = self._finish_failure(reap_error, cleanup_error)
        if not isinstance(failure, BaseExceptionGroup):
            self.fail(f"Expected both failures in a group, received {failure!r}")
        self.assertEqual(failure.message, "Owned cleanup and reap failed")
        self.assertEqual(len(failure.exceptions), 2)
        self.assertIs(failure.exceptions[0], cleanup_error)
        self.assertIs(failure.exceptions[1], reap_error)

    def test_delivered_final_kill_requires_observed_exit(self) -> None:
        # Given KILL really stops the owned child, when the non-consuming exit wait
        # reports no status, then cleanup raises rather than claiming completion.
        wait_exit = runner._wait_exit
        for missing_status in (False, True):
            with self.subTest(missing_status=missing_status):
                child, sentinel = self._ready_child(), self._ready_child()
                owner = runner._Owner(child.pid, os.geteuid(), os.getuid())

                def observe_then_report(
                    pid: int, deadline: float, missing: bool = missing_status
                ) -> int | None:
                    self.assertEqual(wait_exit(pid, deadline), -signal.SIGKILL)
                    return None if missing else -signal.SIGKILL

                with (
                    self._owned_signals(child.pid) as delivered,
                    patch.object(runner, "_wait_exit", side_effect=observe_then_report),
                ):
                    if missing_status:
                        with self.assertRaises(TimeoutError) as raised:
                            runner._kill_owned(owner, runner.monotonic() + 5)
                        self.assertEqual(
                            str(raised.exception), "Owned child survived final SIGKILL"
                        )
                    else:
                        self.assertIsNone(runner._kill_owned(owner, runner.monotonic() + 5))
                self.assertEqual(child.wait(timeout=5), -signal.SIGKILL)
                self._assert_reaped(child, signal.SIGKILL)
                self.assertEqual(delivered, [(child.pid, signal.SIGKILL)])
                self.assertIsNone(sentinel.poll())

    def test_expired_signal_deadline_refuses_delivery_to_live_owned_group(self) -> None:
        # Given a ready live anchor, when cleanup has no remaining time, then no
        # signal is delivered; the same signal with remaining time still works.
        for expired in (True, False):
            with self.subTest(expired=expired):
                child, sentinel = self._ready_child(), self._ready_child()
                owner = runner._Owner(child.pid, os.geteuid(), os.getuid())
                with self._owned_signals(child.pid) as delivered:
                    if expired:
                        with self.assertRaises(TimeoutError) as raised:
                            runner._signal_owned(owner, signal.SIGTERM, 0)
                        self.assertEqual(
                            str(raised.exception), "Owned process cleanup deadline exhausted"
                        )
                        self.assertEqual(delivered, [])
                        self.assertIsNone(child.poll())
                    else:
                        self.assertTrue(
                            runner._signal_owned(owner, signal.SIGTERM, runner.monotonic() + 5)
                        )
                        self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)
                        self._assert_reaped(child, signal.SIGTERM)
                        self.assertEqual(delivered, [(child.pid, signal.SIGTERM)])
                self.assertIsNone(sentinel.poll())


class InspectionFailureContractTest(unittest.TestCase):
    def test_unsupported_platform_refuses_to_construct_inspection_command(self) -> None:
        # Given an unsupported ps dialect, when an inspection is requested, then
        # the command builder refuses instead of silently selecting Darwin flags.
        with (
            patch.object(runner.sys, "platform", "freebsd"),
            self.assertRaises(ValueError) as raised,
        ):
            runner._inspection_command(123)
        self.assertEqual(
            str(raised.exception), "Owned-group inspection requires Darwin or Linux ps"
        )

    def test_unique_anchor_does_not_make_duplicate_descendants_valid(self) -> None:
        # Given exactly one valid actual-parent anchor, when a descendant PID
        # occurs twice, then snapshot validation rejects it; unique rows pass.
        owner = runner._Owner(123, os.geteuid(), os.getuid())
        anchor = runner._Member(123, os.getpid(), 123, 123, owner.uid, owner.ruid, "Z")
        descendant = runner._Member(124, 123, 123, 123, owner.uid, owner.ruid, "Z")
        self.assertIsNone(runner._validate_anchor([anchor, descendant], owner))
        with self.assertRaises(ValueError) as raised:
            runner._validate_anchor([anchor, descendant, descendant], owner)
        self.assertEqual(str(raised.exception), "Duplicate owned-group process identities")
