"""The mutation runner retains its session anchor until owned cleanup is complete."""

import os
import signal
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.validation import mutation_run as runner


class ProgramOwnershipTest(unittest.TestCase):
    def test_zombie_only_owned_group_retains_anchor_until_cleanup_then_reaps(self):
        # Given an exited runner, cleanup must still own its unreaped session leader.
        # This injects Darwin's zombie-only EPERM at the real group-signal boundary;
        # the snapshot and actual-parent reap remain real on both supported platforms.
        anchors = []

        def denied_owned_group(pid, sig):
            observed = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            anchors.append((pid, observed.si_status if observed else None))
            raise PermissionError("injected zombie-only group denial")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(runner.os, "killpg", side_effect=denied_owned_group):
                try:
                    result = runner.program(
                        [sys.executable, "-c", "raise SystemExit(7)"],
                        root,
                        dict(os.environ),
                        5,
                        root / "runner.log",
                    )
                except (ChildProcessError, PermissionError) as error:
                    result = str(error)
            self.assertEqual(result, 7)
            self.assertEqual(len(anchors), 1)
            self.assertEqual(anchors[0][1], 7)
            with self.assertRaises(ChildProcessError):
                os.waitpid(anchors[0][0], os.WNOHANG)

    def test_real_zombie_group_cleanup_preserves_signal_exit_status(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            status = runner.program(
                [sys.executable, "-c", "import os,signal; os.kill(os.getpid(),signal.SIGUSR1)"],
                root,
                dict(os.environ),
                5,
                root / "runner.log",
            )
        self.assertEqual(status, -signal.SIGUSR1)
