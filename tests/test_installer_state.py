import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from nci_si_mcp import installer_state

API: Any = installer_state


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
            (b"{", "malformed"),
            (
                b'{"version":2,"target":"local","components":["server"],"outcomes":[]}',
                "unsupported version",
            ),
            (
                b'{"version":1,"target":"local","components":["server"],"outcomes":[],"extra":1}',
                "malformed",
            ),
            (
                b'{"version":1,"target":"aws","components":["server"],"outcomes":[]}',
                "another selection",
            ),
            (
                b'{"version":1,"target":"local","components":["server","index"],"outcomes":[]}',
                "another selection",
            ),
        )
        for content, message in cases:
            with self.subTest(content=content):
                self.path.write_bytes(content)

                with self.assertRaisesRegex(API.InstallerStateError, message) as error:
                    API.read_state(self.data_dir, "local", ("server",))

                self.assertEqual(self.path.read_bytes(), content)
                self.assertNotIn(content.decode(errors="ignore"), str(error.exception))

    def test_unreadable_state_names_the_file_and_access_next_step(self):
        self.path.write_text("{}", encoding="utf-8")

        with (
            patch.object(Path, "read_text", side_effect=PermissionError("PRIVATE-CANARY")),
            self.assertRaisesRegex(API.InstallerStateError, str(self.path)) as error,
        ):
            API.read_state(self.data_dir, "local", ("server",))

        self.assertIn("Fix access", str(error.exception))
        self.assertNotIn("PRIVATE-CANARY", str(error.exception))

    def test_semantically_invalid_state_is_refused_without_changing_bytes(self):
        cases = (
            b'{"version":true,"target":"local","components":["server"],"outcomes":[]}',
            b'{"version":1,"target":"local","components":["server"],"outcomes":[{"step":"unknown","status":"completed"}]}',
            b'{"version":1,"target":"local","components":["server"],"outcomes":[{"step":"check_prerequisites","status":"completed"},{"step":"check_prerequisites","status":"completed"}]}',
            b'{"version":1,"target":"local","components":["server"],"outcomes":[{"step":"check_prerequisites","status":"blocked"}]}',
            b'{"version":1,"target":"local","components":["server"],"outcomes":[{"step":"check_prerequisites","status":"completed","missing":["x"]}]}',
            b'{"version":1,"target":"local","components":["server","server"],"outcomes":[]}',
            b'{"version":1,"target":"local","components":["alien"],"outcomes":[]}',
        )
        for content in cases:
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaisesRegex(API.InstallerStateError, "malformed"):
                    state = API.read_state(self.data_dir, "local", ("server",))
                    API.validate_outcomes(state, ("check_prerequisites",))
                self.assertEqual(self.path.read_bytes(), content)

    def test_atomic_state_write_fsyncs_and_closes_the_parent_directory(self):
        opened_descriptors = []
        real_open = API.os.open

        def open_directory(*args):
            descriptor = real_open(*args)
            opened_descriptors.append(descriptor)
            return descriptor

        with (
            patch("nci_si_mcp.installer_state.os.open", side_effect=open_directory) as opened,
            patch("nci_si_mcp.installer_state.os.fsync", wraps=API.os.fsync) as fsync,
            patch("nci_si_mcp.installer_state.os.close", wraps=API.os.close) as closed,
        ):
            API.write_state(self.data_dir, self.state)

        opened.assert_any_call(self.data_dir, API.os.O_RDONLY)
        directory_descriptor = opened_descriptors[-1]
        fsync.assert_any_call(directory_descriptor)
        closed.assert_called_once_with(directory_descriptor)
        self.assertEqual(API.read_state(self.data_dir, "local", ("server",)), self.state)

    def test_a_second_owner_cannot_acquire_the_installation_lock(self):
        with (
            API.state_lock(self.data_dir),
            self.assertRaisesRegex(API.InstallerStateError, "already running"),
            API.state_lock(self.data_dir),
        ):
            self.fail("second lock acquired")

    def test_data_directory_access_failures_are_operator_refusals(self):
        file_path = self.data_dir / "not-a-directory"
        file_path.write_text("x", encoding="utf-8")

        with (
            self.assertRaisesRegex(API.InstallerStateError, "fix access"),
            API.state_lock(file_path),
        ):
            self.fail("lock acquired")


if __name__ == "__main__":
    unittest.main()
