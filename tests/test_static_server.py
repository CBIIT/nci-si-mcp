"""Public documentation hosting serves only its static tree and reveals no operational state."""

import io
import json
import threading
import unittest
from contextlib import redirect_stdout
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.static_server import create_server


class StaticServerTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        site = self.root / "site"
        site.mkdir()
        (site / "index.html").write_text("<h1>Public documentation</h1>")
        (site / "empty").mkdir()
        (self.root / "private.txt").write_text("PRIVATE-CANARY")
        (site / "escape.txt").symlink_to(self.root / "private.txt")
        self.server = create_server(site, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode()

    def test_public_pages_and_health_work_without_an_account(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("Public documentation", body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, _, body = self.request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok", "service": "documentation"})

    def test_traversal_symlinks_and_directory_listings_expose_no_private_content(self):
        for path in ("/../private.txt", "/escape.txt", "/empty/"):
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn("PRIVATE-CANARY", body)

    def test_diagnostics_are_json_on_stdout_without_raw_paths_or_query_text(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.request("/absent?PRIVATE-CANARY")
        self.assertNotIn("PRIVATE-CANARY", output.getvalue())
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(records[0]["status"], 404)
        self.assertEqual(records[0]["event"], "documentation_request")

    def test_missing_static_entry_page_fails_before_listening(self):
        with self.assertRaises(ValueError):
            create_server(self.root / "missing", port=0)

    def test_directory_index_symlink_cannot_escape_the_public_tree(self):
        index = self.root / "site/index.html"
        index.unlink()
        index.symlink_to(self.root / "private.txt")
        status, _, body = self.request("/")
        self.assertEqual(status, 404)
        self.assertNotIn("PRIVATE-CANARY", body)

    def test_nested_htm_index_blocks_external_symlinks_but_serves_public_content(self):
        index = self.root / "site/empty/index.htm"
        index.symlink_to(self.root / "private.txt")
        status, _, body = self.request("/empty/")
        self.assertEqual(status, 404)
        self.assertNotIn("PRIVATE-CANARY", body)
        index.unlink()
        index.write_text("<h1>Nested public guide</h1>")
        status, _, body = self.request("/empty/")
        self.assertEqual(status, 200)
        self.assertIn("Nested public guide", body)
