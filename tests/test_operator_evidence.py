"""Controller-recorded execution facts cannot be replaced by a worker's success-looking report."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.operator_evidence import bind_original, finish_evidence
from scripts.portal_store import EvidenceStore, load_bundle

from test_portal_store import run_bundle


class OperatorEvidenceTest(unittest.TestCase):
    def test_shared_bundle_budget_counts_inventory_and_report_bytes(self):
        originals = [
            path.stat().st_size for path in self.bundle.iterdir() if path.name != "report.json"
        ]
        inventory_size = sum(originals)
        report_size = (self.bundle / "report.json").stat().st_size
        for budget, accepted in (
            (inventory_size - 1, False),
            (inventory_size + report_size - 1, False),
            (inventory_size + report_size, True),
        ):
            with (
                self.subTest(budget=budget),
                patch("scripts.operator_evidence.MAX_BUNDLE_BYTES", 65536 + budget),
            ):
                result = finish_evidence(
                    self.job,
                    self.root,
                    {"state": "completed", "exit_code": 0, "reason": None},
                    self.store,
                )
                self.assertEqual(result["reason"], None if accepted else "invalid_evidence")
        self.assertTrue(all(size <= inventory_size - 1 for size in originals))

    def test_report_symlinks_are_invalid_evidence_including_dangling_links(self):
        report = self.bundle / "report.json"
        report.unlink()
        for target in (self.root / "missing", self.bundle / "stories.json"):
            report.symlink_to(target)
            result = finish_evidence(
                self.job,
                self.root,
                {"state": "completed", "exit_code": 0, "reason": None},
                self.store,
            )
            self.assertEqual(
                result, {"state": "failed", "exit_code": 0, "reason": "invalid_evidence"}
            )
            report.unlink()
        self.assertEqual(self.store.history(), [])

    def test_terminal_envelope_facts_preserve_state_and_execution_identity(self):
        for number, (state, code, profile, expected) in enumerate(
            (
                ("failed", None, "acceptance-http-fixture", "interrupted"),
                ("cancelled", None, "acceptance-http-fixture", "cancelled"),
                ("completed", 0, "benchmark-http-remote", "completed"),
                ("completed", 0, "benchmark-http-fixture", "completed"),
            ),
            2,
        ):
            job = self.job | {"run_id": str(number) * 32, "profile": profile}
            result = {"state": state, "exit_code": code, "reason": None}
            finish_evidence(job, self.root, result, self.store)
            saved = json.loads((self.bundle / "envelope.json").read_text())
            self.assertEqual(
                (saved["state"], saved["exit_code"], saved["runner_commit"]),
                (expected, code, "f" * 40),
            )
            self.assertEqual(
                saved["server_commit"], None if profile == "benchmark-http-remote" else "f" * 40
            )

    def test_missing_report_or_inventory_preserves_the_documented_terminal_state(self):
        (self.bundle / "report.json").unlink()
        for number, (state, code) in enumerate((("failed", 1), ("cancelled", None)), 2):
            result = {"state": state, "exit_code": code, "reason": None}
            self.assertEqual(
                finish_evidence(
                    self.job | {"run_id": str(number) * 32}, self.root, result, self.store
                ),
                result,
            )
        (self.bundle / "catalogue.json").unlink()
        result = finish_evidence(
            self.job,
            self.root,
            {"state": "failed", "exit_code": 1, "reason": "worker_failed"},
            self.store,
        )
        self.assertEqual(
            result, {"state": "unavailable", "exit_code": 1, "reason": "missing_inventory"}
        )

    def test_saved_bundle_reimports_idempotently_with_identical_original_bytes(self):
        finish_evidence(
            self.job, self.root, {"state": "completed", "exit_code": 0, "reason": None}, self.store
        )
        self.assertEqual(self.store.import_bundle(load_bundle(self.bundle)), "1" * 32)
        self.assertEqual(len(self.store.history()), 1)

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.job = {
            "run_id": "1" * 32,
            "commit": "f" * 40,
            "profile": "acceptance-http-fixture",
            "started_at": "2026-10-08T10:00:00Z",
        }
        for name, raw in run_bundle().items():
            if name != "envelope":
                (self.bundle / f"{name}.json").write_bytes(raw)
        bind_original(self.root, self.job["commit"])
        self.store = EvidenceStore(self.root / "evidence.sqlite")

    def test_failed_process_stays_failed_even_when_every_reported_case_passed(self):
        result = {"state": "failed", "exit_code": 7, "reason": "worker_failed"}
        finished = finish_evidence(self.job, self.root, result, self.store)
        evidence = self.store.get("1" * 32)["evidence"]
        self.assertEqual(finished, result)
        self.assertEqual((evidence["state"], evidence["exit_code"]), ("failed", 7))
        self.assertEqual(evidence["runner_commit"], "f" * 40)
        self.assertTrue(evidence["inventory_complete"])

    def test_missing_report_is_unavailable_and_does_not_invent_zero_passes(self):
        (self.bundle / "report.json").unlink()
        result = finish_evidence(
            self.job, self.root, {"state": "completed", "exit_code": 0, "reason": None}, self.store
        )
        self.assertEqual(
            result, {"state": "unavailable", "exit_code": 0, "reason": "missing_report"}
        )
        evidence = self.store.get("1" * 32)["evidence"]
        self.assertIsNone(evidence["counts"])
        self.assertFalse(evidence["inventory_complete"])

    def test_changed_original_snapshot_is_rejected_even_if_new_bytes_are_well_formed(self):
        path = self.bundle / "stories.json"
        value = json.loads(path.read_text())
        path.write_text(json.dumps(dict.fromkeys(value, "different-story")))
        result = finish_evidence(
            self.job, self.root, {"state": "completed", "exit_code": 0, "reason": None}, self.store
        )
        self.assertEqual(result, {"state": "failed", "exit_code": 0, "reason": "invalid_evidence"})
        self.assertEqual(self.store.history(), [])

    def test_missing_original_inventory_cannot_be_reconstructed_from_passing_report(self):
        (self.bundle / "catalogue.json").unlink()
        result = finish_evidence(
            self.job, self.root, {"state": "completed", "exit_code": 0, "reason": None}, self.store
        )
        self.assertEqual(
            result, {"state": "unavailable", "exit_code": 0, "reason": "missing_inventory"}
        )
        self.assertEqual(self.store.history(), [])

    def test_a_missing_inventory_does_not_overwrite_an_interrupted_or_cancelled_outcome(self):
        (self.bundle / "catalogue.json").unlink()
        for result in (
            {"state": "interrupted", "exit_code": None, "reason": "owner_shutdown"},
            {"state": "cancelled", "exit_code": 143, "reason": "cancelled"},
        ):
            with self.subTest(state=result["state"]):
                self.assertEqual(finish_evidence(self.job, self.root, result, self.store), result)

    def test_output_bound_failure_preserves_zero_exit_without_claiming_completed(self):
        result = finish_evidence(
            self.job,
            self.root,
            {"state": "failed", "exit_code": 0, "reason": "output_limit"},
            self.store,
        )
        self.assertEqual(result["state"], "failed")
        evidence = self.store.get("1" * 32)["evidence"]
        self.assertEqual(evidence["state"], "interrupted")
        self.assertEqual(evidence["exit_code"], 0)
