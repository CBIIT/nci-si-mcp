"""Persisted jobs are imported without losing their bytes or revealing invalid input."""

import json
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.portal_jobs import JobController


def job(**changes):
    return {
        "run_id": "1" * 32,
        "profile": "acceptance-http-fixture",
        "state": "completed",
        "sequence": 1,
        "commit": "a" * 40,
        "submitted_at": "2026-10-08T10:00:00Z",
        "started_at": "2026-10-08T10:00:00Z",
        "finished_at": "2026-10-08T10:01:00Z",
        "exit_code": 0,
        "reason": None,
    } | changes


class JobImportTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "jobs.json"

    def controller(self):
        return JobController(self.root, execute=self.never_execute, commit=lambda: "a" * 40)

    def never_execute(self, *_args):
        self.fail("Import must not dispatch recorded terminal or invalid jobs")

    def assert_invalid(self, state, path):
        raw = json.dumps(state)
        self.path.write_text(raw)
        with self.assertRaises(OSError) as failure, closing(self.controller()):
            pass
        self.assertEqual(
            str(failure.exception), f"Invalid job field: {path}; preserve jobs.json for inspection"
        )
        self.assertNotIn("PRIVATE-CANARY", str(failure.exception))
        self.assertEqual(self.path.read_text(), raw)

    def test_invalid_job_fields_name_the_location_without_echoing_input(self):
        invalid = (
            [
                (name, "PRIVATE-CANARY", name)
                for name in (
                    "run_id",
                    "profile",
                    "state",
                    "commit",
                    "submitted_at",
                    "started_at",
                    "finished_at",
                    "reason",
                )
            ]
            + [("sequence", value, "sequence") for value in (True, 0)]
            + [("exit_code", value, "exit_code") for value in (True, -1, 256, None, 1)]
            + [
                ("started_at", None, "started_at"),
                ("finished_at", None, "finished_at"),
                ("reason", "deadline", "reason"),
                ("submitted_at", "2026-10-08T10:00:00", "submitted_at"),
                ("submitted_at", "2026-10-08T10:00:00+01:00", "submitted_at"),
                ("submitted_at", None, "submitted_at"),
                ("commit", None, "commit"),
                ("run_id", None, "run_id"),
            ]
        )
        for name, value, path in invalid:
            with self.subTest(name=name, value=value):
                self.assert_invalid({"next": 2, "jobs": [job(**{name: value})]}, f"jobs[0].{path}")
        for name in job():
            row = job()
            del row[name]
            self.assert_invalid({"next": 2, "jobs": [row]}, f"jobs[0].{name}")
        for row in (None, [], job(**{"PRIVATE-CANARY": True})):
            self.assert_invalid({"next": 2, "jobs": [row]}, "jobs[0]")

    def test_invalid_state_fields_and_sequence_relationships_name_the_location(self):
        cases = (
            (None, "state"),
            ({}, "jobs"),
            ({"jobs": []}, "next"),
            ({"next": True, "jobs": []}, "next"),
            ({"next": 0, "jobs": []}, "next"),
            ({"next": 2, "jobs": {}}, "jobs"),
            ({"next": 2, "jobs": [], "PRIVATE-CANARY": True}, "state"),
            ({"next": 1, "jobs": [job()]}, "next"),
            ({"next": 3, "jobs": [job(), job(sequence=2)]}, "jobs[1].run_id"),
            ({"next": 3, "jobs": [job(), job(run_id="2" * 32)]}, "jobs[1].sequence"),
        )
        for state, path in cases:
            with self.subTest(path=path, state=state):
                self.assert_invalid(state, path)

    def test_active_jobs_reject_terminal_facts_and_inconsistent_start(self):
        for state in ("pending", "running"):
            base = job(
                state=state,
                started_at=None if state == "pending" else job()["started_at"],
                finished_at=None,
                exit_code=None,
            )
            for name, value in (
                ("finished_at", job()["finished_at"]),
                ("exit_code", 0),
                ("reason", "deadline"),
            ):
                self.assert_invalid({"next": 2, "jobs": [base | {name: value}]}, f"jobs[0].{name}")
            start = job()["started_at"] if state == "pending" else None
            self.assert_invalid(
                {"next": 2, "jobs": [base | {"started_at": start}]}, "jobs[0].started_at"
            )

    def assert_loads(self, rows, next_sequence):
        self.path.write_text(json.dumps({"next": next_sequence, "jobs": rows}))
        with closing(self.controller()) as controller:
            self.assertEqual(controller.history(), list(reversed(rows)))

    def test_timestamp_exit_sequence_and_identifier_boundaries(self):
        timestamp = "2026-10-08T10:00:00." + "0" * 19 + "Z"
        self.assertEqual(len(timestamp), 40)
        self.assert_loads([job(submitted_at=timestamp)], 2)
        self.assert_invalid(
            {"next": 2, "jobs": [job(submitted_at=timestamp[:-1] + "0Z")]}, "jobs[0].submitted_at"
        )
        for code in (None, 0, 255):
            self.assert_loads([job(state="cancelled", exit_code=code)], 2)
        self.assert_loads([], 1)
        for name, length in (("run_id", 32), ("commit", 40)):
            self.assert_loads([job(**{name: "f" * length})], 2)
            for value in ("f" * (length - 1), "f" * (length + 1), "F" * length):
                self.assert_invalid({"next": 2, "jobs": [job(**{name: value})]}, f"jobs[0].{name}")

    def test_retained_job_limit_accepts_the_limit_and_rejects_one_past(self):
        rows = [job(run_id=f"{index:032x}", sequence=index) for index in range(1, 1004)]
        self.assert_loads(rows, 1004)
        rows.append(job(run_id=f"{1004:032x}", sequence=1004))
        self.assert_invalid({"next": 1005, "jobs": rows}, "jobs")
