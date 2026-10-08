"""Local run controls admit same-origin fixed intents, never commands from another website."""

import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from scripts.portal_http import create_server
from scripts.portal_jobs import JobController
from scripts.portal_store import EvidenceStore


class PortalActionsTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.started = threading.Event()
        self.jobs = JobController(root / "jobs", execute=self.execute, commit=lambda: "a" * 40)
        self.addCleanup(self.jobs.close)
        self.server = create_server(EvidenceStore(root / "store.sqlite"), port=0, jobs=self.jobs)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.intent = {"run_id": "1" * 32, "profile": "benchmark-http-fixture"}

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def execute(self, _job, _directory, cancelled):
        self.started.set()
        cancelled.wait(5)
        return {"state": "cancelled", "exit_code": None, "reason": "cancelled"}

    def request(self, path, *, method="GET", body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode()

    def post(self, path, fields, **headers):
        return self.request(
            path,
            method="POST",
            body=urlencode(fields),
            headers={
                "Origin": self.origin,
                "Content-Type": "application/x-www-form-urlencoded",
                **headers,
            },
        )

    def test_same_origin_can_start_inspect_and_cancel_a_fixed_profile_without_login(self):
        status, _, body = self.request("/jobs")
        self.assertEqual(status, 200)
        self.assertIn("Run checks", body)
        self.assertIn("Uncommitted edits are excluded", body)
        status, headers, _ = self.post("/jobs", self.intent)
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/jobs/" + "1" * 32)
        self.assertTrue(self.started.wait(2))
        status, _, body = self.request(headers["Location"])
        self.assertEqual(status, 200)
        self.assertIn("Cancel this run", body)
        status, _, _ = self.post(headers["Location"] + "/cancel", {"run_id": "1" * 32})
        self.assertEqual(status, 303)
        self.assertTrue(self.jobs.cancelled.is_set())

    def test_other_origins_and_missing_origin_cannot_admit_a_job(self):
        for origin in (None, "null", "https://attacker.invalid", self.origin + "/"):
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            if origin is not None:
                headers["Origin"] = origin
            status, _, _ = self.request(
                "/jobs", method="POST", body=urlencode(self.intent), headers=headers
            )
            self.assertEqual(status, 403)
        self.assertEqual(self.jobs.history(), [])

    def test_run_controls_link_to_local_help_explaining_execution_and_recovery(self):
        _, _, controls = self.request("/jobs")
        status, _, guide = self.request("/help")
        self.assertEqual(status, 200)
        self.assertIn('href="/help#run-controls"', controls)
        self.assertIn('id="run-controls"', guide)
        self.assertIn("900 seconds", guide)
        self.assertIn("240 seconds", guide)
        self.assertIn("never retried automatically", guide)
        self.assertIn("uncommitted edits", guide)
        self.assertNotIn("current dashboard is read-only", guide)

    def test_unknown_fields_and_ambiguous_framing_are_rejected_before_dispatch(self):
        status, _, _ = self.post("/jobs", self.intent | {"command": "unreviewed"})
        self.assertEqual(status, 400)
        status, _, _ = self.post("/jobs", self.intent, **{"Transfer-Encoding": "chunked"})
        self.assertEqual(status, 400)
        self.assertEqual(self.jobs.history(), [])

    def test_cross_site_fetch_metadata_and_rebound_host_are_refused(self):
        for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Host": "attacker.invalid"}):
            status, _, _ = self.post("/jobs", self.intent, **headers)
            self.assertEqual(status, 403)
        self.assertFalse(self.started.is_set())

    def test_bad_form_encoding_media_and_bounds_leave_the_queue_empty(self):
        for body, headers in (
            ("run_id=a&run_id=b", {}),
            ("profile=" + "x" * 1100, {}),
            ("{}", {"Content-Type": "application/json"}),
            (b"profile=\xff", {}),
            ("", {"Content-Length": "-1"}),
        ):
            with self.subTest(body=str(body)[:30]):
                status, _, _ = self.request(
                    "/jobs",
                    method="POST",
                    body=body,
                    headers={
                        "Origin": self.origin,
                        "Content-Type": "application/x-www-form-urlencoded",
                        **headers,
                    },
                )
                self.assertEqual(status, 400)
        self.assertEqual(self.jobs.history(), [])

    def test_queue_full_and_conflicting_intents_report_distinct_recoverable_states(self):
        for number in (1, 2, 3):
            status, _, _ = self.post("/jobs", self.intent | {"run_id": str(number) * 32})
            self.assertEqual(status, 303)
        status, _, _ = self.post("/jobs", self.intent | {"run_id": "4" * 32})
        self.assertEqual(status, 429)
        status, _, _ = self.post("/jobs", self.intent | {"profile": "acceptance-http-fixture"})
        self.assertEqual(status, 409)
        status, _, _ = self.post("/jobs/" + "4" * 32 + "/cancel", {"run_id": "4" * 32})
        self.assertEqual(status, 404)
        self.assertEqual(len(self.jobs.history()), 3)
