"""The local controller owns a bounded queue and preserves every admitted job's state."""

import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import portal_jobs
from scripts.portal_jobs import JobConflictError, JobController, QueueFullError
from scripts.portal_store import EvidenceStore

from test_portal_store import run_bundle


class PortalJobsTest(unittest.TestCase):
    def test_contradictory_execution_records_are_preserved_and_never_replayed(self):
        controller = self.controller()
        self.release.set()
        controller.submit("1" * 32, "benchmark-http-fixture")
        self.wait_for(controller, "1" * 32, "completed")
        controller.close()
        path = self.root / "jobs.json"
        original = json.loads(path.read_text())
        observed = self.observed.copy()
        for changes in (
            {"exit_code": 9},
            {"reason": "deadline"},
            {"started_at": None},
            {"finished_at": None},
            {"state": "pending"},
            {"state": "running"},
            {"state": "cancelled", "finished_at": None},
        ):
            with self.subTest(changes=changes):
                changed = json.loads(json.dumps(original))
                changed["jobs"][0].update(changes)
                raw = json.dumps(changed)
                path.write_text(raw)
                with self.assertRaises(OSError), closing(self.controller()):
                    pass
                self.assertEqual(path.read_text(), raw)
                self.assertEqual(self.observed, observed)

    def test_cancel_during_finalization_preserves_the_saved_terminal_outcome(self):
        controller = self.controller()
        store = EvidenceStore(self.root / "evidence.sqlite")

        def finalizing(job, _directory, _cancelled):
            store.import_bundle(run_bundle(job["run_id"]))
            self.started.set()
            if not self.release.wait(3):
                raise TimeoutError("Test did not release finalization")
            return {"state": "completed", "exit_code": 0, "reason": None}

        controller.execute = finalizing
        controller.submit("1" * 32, "acceptance-http-fixture")
        try:
            self.assertTrue(self.started.wait(2))
            controller.cancel("1" * 32)
        finally:
            self.release.set()
        controller.close()
        saved = store.get("1" * 32)["evidence"]
        recorded = json.loads((self.root / "jobs.json").read_text())["jobs"][0]
        self.assertEqual(recorded["state"], saved["state"])
        self.assertEqual(recorded["state"], "completed")
        self.assertIsNone(recorded["reason"])

    def test_concurrent_identical_submissions_start_only_one_worker(self):
        controller = self.controller()
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(controller.submit, "1" * 32, "benchmark-http-fixture")
                for _ in range(20)
            ]
            sequences = [future.result(timeout=3)["sequence"] for future in futures]
        self.assertTrue(self.started.wait(2))
        self.assertEqual(set(sequences), {1})
        self.assertEqual(self.observed, ["1" * 32])
        self.assertEqual(len(controller.history()), 1)

    def test_worker_io_failure_is_visible_and_does_not_stop_the_next_job(self):
        controller = self.controller()

        def execute(job, _directory, _cancelled):
            if job["run_id"] == "1" * 32:
                raise OSError("PRIVATE-CANARY")
            return {"state": "completed", "exit_code": 0, "reason": None}

        controller.execute = execute
        controller.submit("1" * 32, "benchmark-http-fixture")
        controller.submit("2" * 32, "benchmark-http-fixture")
        row = self.wait_for(controller, "1" * 32, "unavailable")
        self.assertEqual(row["reason"], "worker_error")
        self.assertNotIn("PRIVATE-CANARY", json.dumps(controller.history()))
        self.assertEqual(self.wait_for(controller, "2" * 32, "completed")["exit_code"], 0)

    def test_invalid_persisted_fields_are_unavailable_and_preserved(self):
        controller = self.controller()
        controller.submit("1" * 32, "benchmark-http-fixture")
        controller.close()
        path = self.root / "jobs.json"
        original = json.loads(path.read_text())
        for field, value in (
            ("submitted_at", "not-a-time"),
            ("reason", "PRIVATE-CANARY"),
            ("exit_code", True),
            ("sequence", -1),
            ("finished_at", "2026-01-01T00:00:00"),
            ("profile", "unreviewed-command"),
            ("state", "invented-success"),
            ("run_id", "../outside"),
            ("commit", None),
        ):
            with self.subTest(field=field):
                changed = json.loads(json.dumps(original))
                changed["jobs"][0][field] = value
                raw = json.dumps(changed)
                path.write_text(raw)
                with self.assertRaises(OSError), closing(self.controller()):
                    pass
                self.assertEqual(path.read_text(), raw)

    def test_duplicated_jobs_or_regressed_sequence_are_preserved_without_replay(self):
        controller = self.controller()
        controller.submit("1" * 32, "benchmark-http-fixture")
        controller.close()
        path = self.root / "jobs.json"
        original = json.loads(path.read_text())
        row = original["jobs"][0]
        invalid = (
            {"next": 3, "jobs": [row, row | {"sequence": 2}]},
            {"next": 3, "jobs": [row, row | {"run_id": "2" * 32}]},
            original | {"next": row["sequence"]},
        )
        observed = self.observed.copy()
        for changed in invalid:
            with self.subTest(state=changed):
                raw = json.dumps(changed)
                path.write_text(raw)
                with self.assertRaises(OSError), closing(self.controller()):
                    pass
                self.assertEqual(path.read_text(), raw)
                self.assertEqual(self.observed, observed)

    def test_background_storage_failure_is_unavailable_and_starts_no_worker(self):
        controller = self.controller()
        write = portal_jobs.write_json

        def fail_running(path, state):
            if any(row["state"] == "running" for row in state["jobs"]):
                raise OSError("PRIVATE-CANARY")
            write(path, state)

        with patch.object(portal_jobs, "write_json", side_effect=fail_running):
            controller.submit("1" * 32, "benchmark-http-fixture")
            controller.thread.join(timeout=2)
            with self.assertRaises(OSError):
                controller.history()
        self.assertFalse(controller.thread.is_alive())
        self.assertEqual(self.observed, [])

    def test_shutdown_releases_ownership_even_when_its_final_state_write_fails(self):
        controller = self.controller()
        with (
            patch("scripts.portal_jobs.write_json", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            controller.close()
        self.assertFalse(controller.thread.is_alive())
        replacement = self.controller()
        self.assertEqual(replacement.history(), [])

    def test_failed_durable_admission_cannot_leave_a_runnable_job_in_memory(self):
        controller = self.controller()
        with (
            patch("scripts.portal_jobs.write_json", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            controller.submit("1" * 32, "benchmark-http-fixture")
        self.assertEqual(controller.history(), [])
        self.assertEqual(self.observed, [])

    def test_restart_marks_abandoned_work_interrupted_without_dispatching_it(self):
        controller = self.controller()
        controller.submit("1" * 32, "benchmark-http-fixture")
        self.assertTrue(self.started.wait(2))
        controller.close()
        path = self.root / "jobs.json"
        recorded = json.loads(path.read_text())
        recorded["jobs"][0].update(state="running", finished_at=None, exit_code=None, reason=None)
        path.write_text(json.dumps(recorded))
        reopened = self.controller()
        self.assertEqual(reopened.get("1" * 32)["state"], "interrupted")
        self.assertEqual(self.observed, ["1" * 32])

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.started = threading.Event()
        self.release = threading.Event()
        self.observed = []

    def execute(self, job, _directory, cancelled):
        self.observed.append(job["run_id"])
        self.started.set()
        while not self.release.wait(0.01):
            if cancelled.is_set():
                return {"state": "cancelled", "exit_code": None, "reason": "cancelled"}
        return {"state": "completed", "exit_code": 0, "reason": None}

    def controller(self, **options):
        controller = JobController(
            self.root, execute=self.execute, commit=lambda: "a" * 40, **options
        )
        self.addCleanup(controller.close)
        return controller

    def test_pending_cancellations_cannot_grow_history_past_retention(self):
        controller = self.controller(retention=2)
        controller.submit("1" * 32, "benchmark-http-fixture")
        self.assertTrue(self.started.wait(2))
        for number in (2, 3, 4, 5):
            run_id = str(number) * 32
            controller.submit(run_id, "benchmark-http-fixture")
            controller.cancel(run_id)
        self.assertEqual(
            [row["run_id"] for row in controller.history()], ["5" * 32, "4" * 32, "1" * 32]
        )
        self.assertEqual(self.observed, ["1" * 32])

    def wait_for(self, controller, run_id, state):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            row = controller.get(run_id)
            if row["state"] == state:
                return row
            time.sleep(0.01)
        self.fail(f"Job did not reach {state}: {controller.get(run_id)}")

    def test_one_worker_two_queued_and_duplicate_submission_returns_original_job(self):
        controller = self.controller()
        profile = "benchmark-http-fixture"
        first = controller.submit("1" * 32, profile)
        self.assertTrue(self.started.wait(2))
        controller.submit("2" * 32, profile)
        controller.submit("3" * 32, profile)
        repeated = controller.submit("1" * 32, profile)
        self.assertEqual(repeated["sequence"], first["sequence"])
        with self.assertRaises(QueueFullError):
            controller.submit("4" * 32, profile)
        self.assertEqual(self.observed, ["1" * 32])
        self.assertEqual(len(controller.history()), 3)
        self.release.set()
        self.wait_for(controller, "3" * 32, "completed")
        self.assertEqual(self.observed, ["1" * 32, "2" * 32, "3" * 32])

    def test_duplicate_id_with_changed_profile_and_unreviewed_profile_are_rejected(self):
        controller = self.controller()
        controller.submit("1" * 32, "benchmark-http-fixture")
        with self.assertRaises(JobConflictError):
            controller.submit("1" * 32, "acceptance-http-fixture")
        with self.assertRaises(ValueError):
            controller.submit("2" * 32, "arbitrary-command")
        self.assertEqual(len(controller.history()), 1)

    def test_cancelling_pending_job_never_dispatches_it_and_running_cancel_is_terminal(self):
        controller = self.controller()
        controller.submit("1" * 32, "benchmark-http-fixture")
        self.assertTrue(self.started.wait(2))
        controller.submit("2" * 32, "benchmark-http-fixture")
        self.assertEqual(controller.cancel("2" * 32)["state"], "cancelled")
        controller.cancel("1" * 32)
        row = self.wait_for(controller, "1" * 32, "cancelled")
        self.assertIsNotNone(row["finished_at"])
        self.assertEqual(self.observed, ["1" * 32])

    def test_second_controller_cannot_take_the_same_workspace(self):
        self.controller()
        with self.assertRaisesRegex(RuntimeError, "already owned"):
            JobController(self.root, execute=self.execute, commit=lambda: "a" * 40)
        self.assertEqual(self.observed, [])
