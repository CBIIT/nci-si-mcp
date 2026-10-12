"""Local run controls admit same-origin fixed intents, never commands from another website."""

import socket
import sys
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from scripts.operator_process import run_owned
from scripts.operator_worker import clean_environment
from scripts.portal_http import create_server
from scripts.portal_jobs import JobController
from scripts.portal_store import EvidenceStore


class PortalActionsTest(unittest.TestCase):
    def test_early_eof_cannot_admit_a_valid_form_prefix(self):
        body = urlencode(self.intent)
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.request(
            "POST",
            "/jobs",
            body=body,
            headers={
                "Origin": self.origin,
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body) + 10),
            },
        )
        connection.sock.shutdown(socket.SHUT_WR)
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        self.assertEqual(self.jobs.history(), [])
        self.assertFalse(self.started.is_set())

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

    def test_job_routes_refuse_query_parameters(self):
        self.assertEqual(self.post("/jobs", self.intent)[0], 303)
        for path in ("/jobs?x=1", f"/jobs/{'1' * 32}?x=1", f"/jobs/{'1' * 32}/worker.log?x=1"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 400)
        self.assertEqual(self.request(f"/jobs/{'1' * 32}")[0], 200)


class WorkerLogPageTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.finished = threading.Event()
        self.jobs = JobController(root / "jobs", execute=self.execute, commit=lambda: "a" * 40)
        self.addCleanup(self.jobs.close)
        self.server = create_server(EvidenceStore(root / "store.sqlite"), port=0, jobs=self.jobs)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    close_server = PortalActionsTest.close_server
    request = PortalActionsTest.request

    def execute(self, job, directory, _cancelled):
        directory.mkdir()
        log = directory / "worker.log"
        if job["run_id"] == "1" * 32:
            log.write_bytes(b"Traceback: <worker> & failed\n")
        elif job["run_id"] == "4" * 32:
            (directory / "private").write_text("PRIVATE-CANARY")
            log.symlink_to(directory / "private")
        elif job["run_id"] == "5" * 32:
            log.write_bytes(b"x" * (2 * 512 * 1024 + 65))
        elif job["run_id"] == "6" * 32:
            log.mkdir()
        elif job["run_id"] == "7" * 32:
            # The real copier keeps the first and the last 512 KiB of 3 MiB.
            run_owned(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 3 * 2**20)"],
                directory=directory,
                environment=clean_environment(directory),
                seconds=30,
                cancelled=threading.Event(),
            )
        self.finished.set()
        return {"state": "failed", "exit_code": 1, "reason": "worker_failed"}

    def run_job(self, run_id):
        self.finished.clear()
        self.jobs.submit(run_id, "benchmark-http-fixture")
        self.assertTrue(self.finished.wait(5))
        deadline = time.monotonic() + 5
        while self.jobs.get(run_id)["state"] != "failed" and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_the_job_page_links_the_worker_log_which_is_served_as_plain_text(self):
        self.run_job("1" * 32)
        status, _, page = self.request("/jobs/" + "1" * 32)
        self.assertEqual(status, 200)
        self.assertIn(f'<a href="/jobs/{"1" * 32}/worker.log">Worker log</a>', page)
        status, headers, body = self.request(f"/jobs/{'1' * 32}/worker.log")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(body, "Traceback: <worker> & failed\n")

    def test_a_job_without_a_worker_log_says_so_and_serves_none(self):
        self.run_job("2" * 32)
        _, _, page = self.request("/jobs/" + "2" * 32)
        self.assertIn("No worker log was recorded.", page)
        self.assertNotIn("/worker.log", page)
        self.assertEqual(self.request(f"/jobs/{'2' * 32}/worker.log")[0], 404)
        self.assertEqual(self.request(f"/jobs/{'3' * 32}/worker.log")[0], 404)

    def test_only_the_exact_worker_log_path_serves_the_log(self):
        self.run_job("1" * 32)
        for path in ("workerXlog", "worker-log", "worker.log/", "worker.logs"):
            with self.subTest(path=path):
                self.assertEqual(self.request(f"/jobs/{'1' * 32}/{path}")[0], 404)

    def test_a_linked_log_is_no_log_and_an_oversized_one_is_unusable_evidence(self):
        self.run_job("4" * 32)
        status, _, page = self.request("/jobs/" + "4" * 32)
        self.assertEqual(status, 200)
        self.assertIn("No worker log was recorded.", page)
        status, _, body = self.request(f"/jobs/{'4' * 32}/worker.log")
        self.assertEqual(status, 404)
        self.assertNotIn("PRIVATE-CANARY", body)
        self.run_job("5" * 32)
        status, _, page = self.request("/jobs/" + "5" * 32)
        self.assertEqual(status, 200)
        self.assertIn(f'<a href="/jobs/{"5" * 32}/worker.log">Worker log</a>', page)
        self.assertEqual(self.request(f"/jobs/{'5' * 32}/worker.log")[0], 503)

    def test_a_directory_named_like_the_log_is_no_log(self):
        self.run_job("6" * 32)
        _, _, page = self.request("/jobs/" + "6" * 32)
        self.assertIn("No worker log was recorded.", page)
        self.assertNotIn("/worker.log", page)
        self.assertEqual(self.request(f"/jobs/{'6' * 32}/worker.log")[0], 404)

    def test_a_log_at_the_largest_size_the_copier_writes_is_served(self):
        self.run_job("7" * 32)
        status, _, body = self.request(f"/jobs/{'7' * 32}/worker.log")
        self.assertEqual(status, 200)
        omitted = f"\n[worker.log: {2 * 2**20} bytes omitted]\n"
        self.assertEqual(body, "x" * 512 * 1024 + omitted + "x" * 512 * 1024)
