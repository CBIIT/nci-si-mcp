import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import nci_si_mcp

API: Any = getattr(nci_si_mcp, "installer_state", None)


class InstallerStateTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data_dir = Path(directory.name)
        self.path = self.data_dir / "installer-state.json"
        self.state = API.InstallationState(
            version=1,
            target="local",
            components=("server",),
            outcomes=(API.StepOutcome("check_prerequisites", "completed"),),
        )

    def test_state_round_trips_through_the_closed_versioned_json_record(self):
        API.write_state(self.data_dir, self.state)

        self.assertEqual(API.read_state(self.data_dir, "local", ("server",)), self.state)
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8")),
            {
                "version": 1,
                "target": "local",
                "components": ["server"],
                "outcomes": [{"step": "check_prerequisites", "status": "completed"}],
            },
        )

    def test_failed_atomic_replace_preserves_the_previous_state(self):
        original = b'{"old":"state"}\n'
        self.path.write_bytes(original)

        with (
            patch("nci_si_mcp.installer_state.os.replace", side_effect=OSError("PRIVATE-CANARY")),
            self.assertRaisesRegex(OSError, "PRIVATE-CANARY"),
        ):
            API.write_state(self.data_dir, self.state)

        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.data_dir.glob(".installer-state.*.tmp")), [])

    def test_invalid_or_mismatched_state_fails_closed_without_rewriting(self):
        cases = (
            b"{",
            b'{"version":2,"target":"local","components":["server"],"outcomes":[]}',
            b'{"version":1,"target":"local","components":["server"],"outcomes":[],"extra":1}',
            b'{"version":1,"target":"aws","components":["server"],"outcomes":[]}',
            b'{"version":1,"target":"local","components":["server","index"],"outcomes":[]}',
        )
        for content in cases:
            with self.subTest(content=content):
                self.path.write_bytes(content)

                with self.assertRaisesRegex(ValueError, "new --data-dir") as error:
                    API.read_state(self.data_dir, "local", ("server",))

                self.assertEqual(self.path.read_bytes(), content)
                self.assertNotIn(content.decode(errors="ignore"), str(error.exception))

    def test_a_second_owner_cannot_acquire_the_installation_lock(self):
        with (
            API.state_lock(self.data_dir),
            self.assertRaisesRegex(RuntimeError, "already running"),
            API.state_lock(self.data_dir),
        ):
            self.fail("second lock acquired")


if __name__ == "__main__":
    unittest.main()
