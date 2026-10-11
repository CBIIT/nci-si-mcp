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

    def test_evidence_files_are_compact_with_sorted_keys(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "state.json"
            write_json(target, {"b": [1, 2], "a": "é"})
            self.assertEqual(target.read_bytes(), b'{"a": "\\u00e9", "b": [1, 2]}')

    def test_reports_are_indented_in_insertion_order_and_end_with_a_newline(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "nested/report.json"
            write_json(target, {"b": [1], "a": {}}, form="report")
            self.assertEqual(target.read_bytes(), b'{\n  "b": [\n    1\n  ],\n  "a": {}\n}\n')

    def test_a_reader_of_the_previous_report_keeps_it_whole_across_a_rewrite(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "report.json"
            target.write_text('{"complete": false}\n')
            with target.open() as reader:
                write_json(target, {"complete": True}, form="report")
                self.assertEqual(reader.read(), '{"complete": false}\n')
            self.assertEqual(target.read_text(), '{\n  "complete": true\n}\n')

    def test_output_symlink_is_rejected_and_its_target_left_alone(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "private"
            private.write_bytes(b"PRIVATE-CANARY")
            link = root / "report.json"
            link.symlink_to(private)
            with self.assertRaisesRegex(ValueError, "symlink"):
                write_json(link, {"complete": True}, form="report")
            self.assertEqual(private.read_bytes(), b"PRIVATE-CANARY")
            self.assertTrue(link.is_symlink())

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
