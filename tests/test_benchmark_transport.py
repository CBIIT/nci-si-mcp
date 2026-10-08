"""Real HTTP guard tests: credentials stay on the approved endpoint and work is bounded."""

import asyncio
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scripts.benchmark_limits import Budget, Limits, RunStoppedError
from scripts.benchmark_transport import http_client


class BenchmarkTransportTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.response = (200, {}, b'{"ok":true}')
        self.content_length = True
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append((self.path, self.headers.get("Authorization")))
                status, headers, body = owner.response
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                if owner.content_length:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def get(
        self, *, url=None, budget=None, max_bytes=1024, authorization="Bearer test-only-canary"
    ):
        async def scenario():
            async with http_client(
                self.url,
                budget or Budget(Limits()),
                authorization=authorization,
                max_bytes=max_bytes,
            ) as client:
                return await client.get(url or self.url)

        return asyncio.run(scenario())

    def test_actual_response_and_authorization_are_bound_to_exact_endpoint(self):
        budget = Budget(Limits())
        response = self.get(budget=budget)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(self.requests, [("/mcp", "Bearer test-only-canary")])
        self.assertEqual(budget.requests, 1)

    def test_different_path_is_rejected_before_any_request(self):
        with self.assertRaisesRegex(RunStoppedError, "target"):
            self.get(url=self.url + "/other")
        self.assertEqual(self.requests, [])

    def test_redirect_is_not_followed_even_on_the_same_origin(self):
        self.response = (307, {"Location": self.url + "/elsewhere"}, b"")
        with self.assertRaisesRegex(RunStoppedError, "redirect"):
            self.get()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0][0], "/mcp")

    def test_response_size_and_compressed_content_fail_before_decoding(self):
        self.response = (200, {}, b"x" * 100)
        with self.assertRaisesRegex(RunStoppedError, "response_limit"):
            self.get(max_bytes=32)
        self.response = (200, {"Content-Encoding": "gzip"}, b"invalid compressed body")
        with self.assertRaisesRegex(RunStoppedError, "content_encoding"):
            self.get()
        self.assertEqual(len(self.requests), 2)

    def test_exhaustion_and_cancelled_budget_send_nothing(self):
        budget = Budget(Limits(max_requests=1))
        budget.admit()
        with self.assertRaisesRegex(RunStoppedError, "request_budget"):
            self.get(budget=budget)
        budget.cancelled.set()
        with self.assertRaisesRegex(RunStoppedError, "cancelled"):
            self.get(budget=budget)
        self.assertEqual(self.requests, [])

    def test_missing_content_length_cannot_bypass_stream_limit(self):
        self.content_length = False
        self.response = (200, {}, b"x" * 100)
        with self.assertRaisesRegex(RunStoppedError, "response_limit"):
            self.get(max_bytes=32, authorization=None)
        self.assertEqual(self.requests, [("/mcp", None)])

    def test_auth_refusal_reports_status_without_consuming_or_exposing_error_body(self):
        self.response = (403, {}, b"PRIVATE-CANARY")
        with self.assertRaisesRegex(RunStoppedError, "http_403") as failure:
            self.get()
        self.assertNotIn("PRIVATE-CANARY", str(failure.exception))
        self.assertEqual(len(self.requests), 1)
