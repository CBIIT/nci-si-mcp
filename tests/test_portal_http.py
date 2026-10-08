"""Real loopback HTTP preserves the local-only boundary and serves readable result pages."""

import sqlite3
import threading
import unittest
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.portal_http import create_server
from scripts.portal_store import EvidenceStore

from test_portal_store import run_bundle


class PortalHTTPTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = EvidenceStore(Path(self.directory.name) / "results.sqlite")
        self.store.import_bundle(run_bundle())
        self.server = create_server(self.store, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, *, method="GET", headers=None, raw=False):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        return response.status, dict(response.getheaders()), body if raw else body.decode()

    def test_local_typography_serves_exact_font_bytes_with_self_only_policy(self):
        status, headers, body = self.request("/assets/open-sans.ttf", raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(body, Path("docs/site-assets/fonts/open-sans.ttf").read_bytes())
        self.assertEqual(headers["Content-Type"], "font/ttf")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertIn("font-src 'self'", headers["Content-Security-Policy"])
        _, _, page = self.request("/")
        self.assertIn('url("/assets/open-sans.ttf")', page)
        self.assertIn("font-family:Poppins", page)
        self.assertIn('font-family:"Roboto Mono"', page)

    def test_assets_expose_only_reviewed_fonts_and_their_licences(self):
        status, _, body = self.request("/assets/poppins-OFL.txt")
        self.assertEqual(status, 200)
        self.assertIn("SIL OPEN FONT LICENSE", body)
        for path in ("/assets/../../pyproject.toml", "/assets/evidence.sqlite", "/assets/"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/assets/open-sans.ttf?path=private")[0], 400)

    def test_unreadable_font_reports_asset_repair_without_disclosing_filesystem_details(self):
        with patch("scripts.portal_http.Path.read_bytes", side_effect=OSError("PRIVATE-CANARY")):
            status, _, body = self.request("/assets/open-sans.ttf")
        self.assertEqual(status, 503)
        self.assertIn("Local assets unavailable", body)
        self.assertNotIn("PRIVATE-CANARY", body)
        self.assertNotIn("Check the local evidence store", body)

    def test_history_and_filtered_results_are_accessible_without_login(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("Validation history", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        status, _, body = self.request("/runs/" + "1" * 32 + "?tool=lookup")
        self.assertEqual(status, 200)
        self.assertIn("1 of 2 cases", body)
        self.assertIn("Origin unverified", body)

    def test_local_help_explains_result_limits_and_contextual_links_resolve(self):
        status, _, help_page = self.request("/help")
        self.assertEqual(status, 200)
        for anchor in ("getting-started", "run-status", "acceptance", "provenance", "benchmarks"):
            with self.subTest(anchor=anchor):
                self.assertIn(f'id="{anchor}"', help_page)
        self.assertIn("No login is required locally", help_page)
        self.assertIn("p95", help_page)
        self.assertIn("not proof of live-service readiness", help_page)
        _, _, history = self.request("/")
        self.assertIn('href="/help#run-status"', history)
        self.assertIn('href="/help#getting-started"', history)
        _, _, run = self.request("/runs/" + "1" * 32)
        self.assertIn('href="/help#acceptance"', run)
        self.assertIn('href="/help#provenance"', run)

    def test_host_rebinding_and_mutations_are_rejected(self):
        status, _, body = self.request("/", headers={"Host": "attacker.invalid"})
        self.assertEqual(status, 403)
        self.assertNotIn("Validation history", body)
        status, _, _ = self.request("/", method="POST")
        self.assertEqual(status, 405)

    def test_unknown_run_path_and_duplicate_filter_fail_explicitly(self):
        for path, expected in (
            ("/runs/" + "2" * 32, 404),
            ("/../../etc/passwd", 404),
            ("/runs/" + "1" * 32 + "?tool=a&tool=b", 400),
        ):
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, expected)
                self.assertNotIn("Traceback", body)

    def test_health_and_csp_do_not_expose_evidence_or_enable_external_scripts(self):
        status, headers, body = self.request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, '{"status":"ok","deployment":"local-only"}')
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_bad_filters_and_absolute_targets_are_rejected(self):
        for path in (
            "http://example.org/",
            "/" + "a" * 2050,
            "/runs/" + "1" * 32 + "?unknown=a",
            "/compare?left=missing",
        ):
            with self.subTest(path=path[:80]):
                status, _, _ = self.request(
                    path, headers={"Host": f"127.0.0.1:{self.server.server_port}"}
                )
                self.assertEqual(status, 400)

    def test_comparison_route_preserves_wrong_kind_error(self):
        status, _, body = self.request("/compare?left=" + "1" * 32 + "&right=" + "1" * 32)
        self.assertEqual(status, 200)
        self.assertIn("Comparison unavailable", body)

    def test_storage_failure_is_unavailable_not_empty_history(self):
        with patch.object(
            self.store, "history", side_effect=sqlite3.OperationalError("PRIVATE-CANARY")
        ):
            status, _, body = self.request("/")
        self.assertEqual(status, 503)
        self.assertIn("Evidence unavailable", body)
        self.assertNotIn("PRIVATE-CANARY", body)

    def test_corrupted_stored_json_is_not_misreported_as_a_caller_filter_error(self):
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            connection.execute("UPDATE runs SET projection='invalid JSON'")
        status, _, body = self.request("/runs/" + "1" * 32)
        self.assertEqual(status, 503)
        self.assertIn("Evidence unavailable", body)

    def test_non_record_history_json_reports_storage_failure(self):
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            connection.execute("UPDATE runs SET summary='[]'")
        status, _, body = self.request("/")
        self.assertEqual(status, 503)
        self.assertNotIn("No recorded attempt", body)

    def test_interrupted_without_report_shows_unknown_cases_not_zero_successes(self):
        self.store.import_bundle(run_bundle("2" * 32, "interrupted"))
        status, _, body = self.request("/runs/" + "2" * 32)
        self.assertEqual(status, 200)
        self.assertIn("No report was produced", body)
        self.assertIn("2 of 2 cases", body)
        self.assertIn("unknown", body)
