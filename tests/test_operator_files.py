"""Evidence writes preserve previous state on failure and reads enforce their boundary."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.operator_files import read_bytes, write_json


class OperatorFilesTest(unittest.TestCase):
    def test_failed_serialization_preserves_previous_state_and_removes_partial_file(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "state.json"
            target.write_text('{"complete": true}')
            with self.assertRaises(TypeError):
                write_json(target, {"first": 1, "unsupported": object()})
            self.assertEqual(target.read_text(), '{"complete": true}')
            self.assertEqual(list(root.iterdir()), [target])

    def test_read_accepts_exact_limit_and_rejects_one_extra_byte(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "report.json"
            target.write_bytes(b"1234")
            self.assertEqual(read_bytes(target, 4), b"1234")
            target.write_bytes(b"12345")
            with self.assertRaisesRegex(ValueError, "size bound"):
                read_bytes(target, 4)

    def test_symlink_is_rejected_without_reading_or_modifying_its_target(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "private"
            private.write_bytes(b"PRIVATE-CANARY")
            link = root / "report.json"
            link.symlink_to(private)
            with self.assertRaisesRegex(ValueError, "symlink"):
                read_bytes(link, 100)
            self.assertEqual(private.read_bytes(), b"PRIVATE-CANARY")
