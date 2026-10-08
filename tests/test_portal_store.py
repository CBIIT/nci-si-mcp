"""Local sequence, immutable imports and incomplete attempts keep dashboard history honest."""

import hashlib
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.portal_store import EvidenceStore, load_bundle

from test_evidence_acceptance import bundle, encoded, report
from test_evidence_benchmark import benchmark
from test_evidence_envelope import envelope


def run_bundle(run_id="1" * 32, state="completed", timestamp="2026-10-01T00:00:00Z"):
    snapshots = bundle()
    raw = encoded(report()) if state == "completed" else None
    metadata = envelope(
        run_id=run_id,
        state=state,
        started_at=timestamp,
        finished_at=timestamp,
        exit_code=0 if raw else None,
        report_sha256=hashlib.sha256(raw).hexdigest() if raw else None,
    )
    metadata.update(
        {key + "_sha256": hashlib.sha256(value).hexdigest() for key, value in snapshots.items()}
    )
    result = snapshots | {"envelope": encoded(metadata)}
    if raw is not None:
        result["report"] = raw
    return result


class EvidenceStoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "evidence.sqlite"
        self.store = EvidenceStore(self.path, retention=2)

    def test_import_preserves_original_projection_and_marks_origin_unverified(self):
        self.store.import_bundle(run_bundle())
        record = self.store.get("1" * 32)
        self.assertEqual(record["origin"], "local-import-unverified")
        self.assertEqual(record["evidence"]["tools"]["lookup"]["outcome"], "PASS")
        self.assertEqual(record["evidence"]["cases"][0]["story"], "find-a-concept")
        self.assertIsNone(record["evidence"]["server_commit"])

    def test_last_attempt_is_separate_from_last_complete_evidence(self):
        self.store.import_bundle(run_bundle(timestamp="2099-01-01T00:00:00Z"))
        self.store.import_bundle(run_bundle("2" * 32, "interrupted"))
        history = self.store.history()
        self.assertEqual(history[0]["run_id"], "2" * 32)
        self.assertEqual(self.store.latest()["attempt"]["run_id"], "2" * 32)
        self.assertEqual(self.store.latest()["complete"]["run_id"], "1" * 32)
        self.assertFalse(history[0]["inventory_complete"])

    def test_identical_reimport_is_idempotent_and_conflicting_id_is_rejected(self):
        self.store.import_bundle(run_bundle())
        self.store.import_bundle(run_bundle())
        self.assertEqual(len(self.store.history()), 1)
        with self.assertRaisesRegex(ValueError, "already names different evidence"):
            self.store.import_bundle(run_bundle(state="cancelled"))
        self.assertEqual(self.store.get("1" * 32)["evidence"]["state"], "completed")

    def test_retention_prunes_oldest_local_sequence_and_survives_reopening(self):
        for number in range(1, 4):
            self.store.import_bundle(run_bundle(str(number) * 32))
        reopened = EvidenceStore(self.path, retention=2)
        self.assertEqual([row["run_id"] for row in reopened.history()], ["3" * 32, "2" * 32])
        with self.assertRaises(KeyError):
            reopened.get("1" * 32)

    def test_concurrent_identical_imports_leave_one_immutable_record(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            identifiers = list(workers.map(self.store.import_bundle, [run_bundle()] * 4))
        self.assertEqual(identifiers, ["1" * 32] * 4)
        self.assertEqual(len(self.store.history()), 1)
        self.assertEqual(self.store.get("1" * 32)["evidence"]["state"], "completed")

    def test_failed_retention_rolls_back_new_import_and_preserves_previous_evidence(self):
        self.store.import_bundle(run_bundle())
        self.store.import_bundle(run_bundle("2" * 32))
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("""CREATE TRIGGER prevent_delete BEFORE DELETE ON runs
                BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.import_bundle(run_bundle("3" * 32))
        self.assertEqual([row["run_id"] for row in self.store.history()], ["2" * 32, "1" * 32])
        with self.assertRaises(KeyError):
            self.store.get("3" * 32)

    def test_invalid_evidence_never_enters_history(self):
        records = run_bundle()
        records["report"] = b"{}"
        with self.assertRaises(ValueError):
            self.store.import_bundle(records)
        self.assertEqual(self.store.history(), [])
        self.assertEqual(self.store.latest(), {"attempt": None, "complete": None})

    def test_legacy_import_cannot_become_valid_complete_or_a_pass(self):
        run_id = self.store.import_legacy(b'{"outcome":"PASS","secret":"CANARY"}', "acceptance")
        record = self.store.get(run_id)
        self.assertEqual(record["evidence"]["state"], "unverified")
        self.assertIsNone(self.store.latest()["complete"])
        self.assertNotIn("CANARY", json.dumps(record))
        self.assertNotIn("PASS", json.dumps(record))

    def test_retention_and_kind_are_closed_inputs(self):
        for value in (0, 1001, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                EvidenceStore(self.path, retention=value)
        with self.assertRaises(ValueError):
            self.store.import_legacy(b"{}", "arbitrary")

    def test_benchmark_import_preserves_measured_errors_and_unknown_fingerprint(self):
        raw = encoded(benchmark())
        metadata = envelope(kind="benchmark", report_sha256=hashlib.sha256(raw).hexdigest())
        self.store.import_bundle({"envelope": encoded(metadata), "report": raw})
        evidence = self.store.get("1" * 32)["evidence"]
        self.assertEqual(evidence["cases"][0]["warm"]["errors"], 1)
        self.assertFalse(evidence["comparison_ready"])

    def test_incomplete_bundle_unknown_file_and_size_limits_reject_import(self):
        incomplete = run_bundle()
        del incomplete["catalogue"]
        for records in (
            {},
            run_bundle() | {"extra": b"{}"},
            {"envelope": run_bundle()["envelope"]},
            incomplete,
        ):
            with self.subTest(files=list(records)), self.assertRaises(ValueError):
                self.store.import_bundle(records)
        with patch("scripts.portal_store.MAX_BUNDLE_BYTES", 1), self.assertRaises(ValueError):
            self.store.import_bundle(run_bundle())
        self.assertEqual(self.store.history(), [])

    def test_large_projection_does_not_partially_insert(self):
        # A small source can expand into many selected-but-unreported case rows.
        with (
            patch(
                "scripts.portal_store._project",
                return_value={"run_id": "1" * 32, "cases": "x" * 100},
            ),
            patch("scripts.portal_store.MAX_BUNDLE_BYTES", 50),
            self.assertRaises(ValueError),
        ):
            self.store.import_bundle({"envelope": b"{}"})
        self.assertEqual(self.store.history(), [])

    def test_file_loader_skips_unlisted_files_and_missing_optional_report(self):
        directory = self.path.parent / "bundle"
        directory.mkdir()
        records = run_bundle(state="interrupted")
        for name, raw in records.items():
            (directory / (name + ".json")).write_bytes(raw)
        (directory / "secret.txt").write_text("CANARY")
        self.assertEqual(load_bundle(directory), records)

    def test_file_loader_rejects_root_and_file_symlinks(self):
        directory = self.path.parent / "bundle"
        directory.mkdir()
        alias = self.path.parent / "alias"
        alias.symlink_to(directory, target_is_directory=True)
        with self.assertRaises(ValueError):
            load_bundle(alias)
        (directory / "envelope.json").symlink_to(self.path)
        with self.assertRaises(ValueError):
            load_bundle(directory)

    def test_file_loader_bounds_reads_before_loading_whole_file(self):
        directory = self.path.parent / "bundle"
        directory.mkdir()
        (directory / "envelope.json").write_bytes(b"x" * 100)
        with patch("scripts.portal_store.MAX_BUNDLE_BYTES", 8), self.assertRaises(ValueError):
            load_bundle(directory)
