"""The HTTP client against a real local server, so that what urllib puts on the wire is tested.

A request the server saw is a request the client made: the attempt counts are checked against it.
"""

import json
import socket
import threading
import time
import unittest
from dataclasses import dataclass, field
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar
from unittest.mock import patch

from nci_si_mcp.errors import PlatformError, correlated
from nci_si_mcp.evs import LICENSE_KEY_HEADER, EVSClient, EVSResponseError
from nci_si_mcp.http_client import (
    MAX_RETRY_DELAY_SECONDS,
    HttpClient,
    UpstreamRejectedError,
    UpstreamTooLargeError,
    UpstreamUnavailableError,
)

KEY = "placeholder-licence-key"
OK = b'{"ok": true}'


@dataclass
class Reply:
    status: int = 200
    body: bytes = OK
    headers: dict = field(default_factory=dict)
    drop: bool = False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        reply = self.server.script.pop(0) if self.server.script else Reply()
        if reply.drop:
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.send_response(reply.status)
        for name, value in reply.headers.items():
            self.send_header(name, value(self.server) if callable(value) else value)
        self.send_header("Content-Length", str(len(reply.body)))
        self.end_headers()
        self.wfile.write(reply.body)

    def log_message(self, *_):
        pass


class ServerTestCase(unittest.TestCase):
    def serve(self, *script):
        """A server that answers with `script` in turn, then with OK; returns it."""

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.script, server.seen = list(script), []
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        server.url = f"http://127.0.0.1:{server.server_port}"
        return server

    def client(self, server, **options):
        settings = {
            "label": "EVS",
            "timeout_seconds": 5,
            "max_attempts": 3,
            "retry_backoff_seconds": 0.25,
            "max_response_bytes": 1000,
            "size_bound": "BOUND",
        } | options
        client = HttpClient(server.url, **settings)
        self.waits = []
        client.sleep = self.waits.append
        client.jitter = lambda low, high: high
        return client

    def failure(self, client, error=UpstreamUnavailableError, path="/x"):
        with self.assertRaises(error) as raised:
            client.get_json(path)
        return raised.exception


class HeadersTest(ServerTestCase):
    def test_every_request_asks_for_json_and_carries_the_identifier_of_the_call(self):
        server = self.serve(Reply(503), Reply(503))

        with correlated("call-17"):
            self.client(server).get_json("/x")

        self.assertEqual(len(server.seen), 3)
        for _, headers in server.seen:
            self.assertEqual(headers["accept"], "application/json")
            self.assertEqual(headers["x-correlation-id"], "call-17")

    def test_a_request_outside_any_call_carries_no_identifier(self):
        server = self.serve()

        self.client(server).get_json("/x")

        self.assertNotIn("x-correlation-id", server.seen[0][1])

    def test_the_query_is_sent_without_empty_parameters(self):
        server = self.serve()

        self.client(server).get_json("/x", {"list": "C1,C2", "include": None})

        self.assertEqual(server.seen[0][0], "/x?list=C1%2CC2")


class RetryTest(ServerTestCase):
    def test_a_server_error_and_a_dropped_connection_are_retried_until_one_succeeds(self):
        server = self.serve(Reply(500), Reply(drop=True), Reply(502))

        result = self.client(server, max_attempts=4).get_json("/x")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(server.seen), 4)

    def test_a_client_error_is_never_retried(self):
        for status in (400, 401, 403, 404, 410):
            with self.subTest(status=status):
                server = self.serve(Reply(status))

                error = self.failure(self.client(server), UpstreamRejectedError)

                self.assertEqual(len(server.seen), 1)
                self.assertEqual(error.details["status"], status)
                self.assertEqual(self.waits, [])

    def test_every_attempt_is_counted_and_reported(self):
        server = self.serve(Reply(503), Reply(503), Reply(503), Reply(503))

        error = self.failure(self.client(server, max_attempts=4))

        self.assertEqual(len(server.seen), 4)
        self.assertEqual(
            error.details,
            {"surface": "evs", "attempts": 4, "status": 503},
        )

    def test_a_rejection_after_retries_counts_the_attempts_before_it(self):
        server = self.serve(Reply(503), Reply(403))

        error = self.failure(self.client(server), UpstreamRejectedError)

        self.assertEqual(error.details, {"surface": "evs", "status": 403, "attempts": 2})
        self.assertEqual(len(server.seen), 2)

    def test_the_backoff_doubles_with_jitter_between_half_and_all_of_it(self):
        server = self.serve(Reply(503), Reply(503), Reply(503))
        bounds = []

        def lowest(low, high):
            bounds.append((low, high))
            return low

        client = self.client(server, max_attempts=4)
        client.jitter = lowest
        client.get_json("/x")

        self.assertEqual(bounds, [(0.5, 1.0)] * 3)
        self.assertEqual(self.waits, [0.125, 0.25, 0.5])

    def test_a_wait_never_exceeds_the_cap(self):
        server = self.serve(Reply(503), Reply(503))

        self.client(server, retry_backoff_seconds=3600).get_json("/x")

        self.assertEqual(self.waits, [MAX_RETRY_DELAY_SECONDS, MAX_RETRY_DELAY_SECONDS])


class RetryAfterTest(ServerTestCase):
    def test_a_429_is_asked_again_after_the_seconds_it_names(self):
        server = self.serve(Reply(429, headers={"Retry-After": "7"}))

        result = self.client(server).get_json("/x")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.waits, [7.0])
        self.assertEqual(len(server.seen), 2)

    def test_a_429_is_asked_again_after_the_date_it_names(self):
        date = formatdate(time.time() + 30, usegmt=True)
        server = self.serve(Reply(429, headers={"Retry-After": date}))

        self.client(server).get_json("/x")

        (wait,) = self.waits
        self.assertAlmostEqual(wait, 29, delta=1.5)

    def test_a_date_that_has_passed_and_a_value_that_means_nothing_fall_back_to_the_backoff(self):
        past = formatdate(time.time() - 3600, usegmt=True)
        for value in (past, "soon", "-5", ""):
            with self.subTest(value=value):
                server = self.serve(Reply(429, headers={"Retry-After": value}))

                self.client(server).get_json("/x")

                self.assertEqual(self.waits, [0.25])

    def test_the_longer_of_the_wait_named_and_the_backoff_is_taken(self):
        server = self.serve(Reply(503, headers={"Retry-After": "1"}))

        self.client(server, retry_backoff_seconds=4).get_json("/x")

        self.assertEqual(self.waits, [4.0])

    def test_a_wait_longer_than_the_cap_is_not_waited_and_not_asked_again(self):
        server = self.serve(Reply(429, headers={"Retry-After": "3600"}))

        error = self.failure(self.client(server))

        self.assertEqual(len(server.seen), 1)
        self.assertEqual(self.waits, [])
        self.assertEqual(
            error.details, {"surface": "evs", "attempts": 1, "status": 429, "retryAfter": "3600"}
        )

    def test_a_429_repeated_until_the_attempts_are_used_reports_them(self):
        server = self.serve(*[Reply(429, headers={"Retry-After": "1"})] * 3)

        error = self.failure(self.client(server))

        self.assertEqual(len(server.seen), 3)
        self.assertEqual(error.details["attempts"], 3)
        self.assertEqual(error.details["retryAfter"], "1")


class ClassificationTest(ServerTestCase):
    def test_a_failure_masked_as_a_success_is_an_upstream_error_and_not_retried(self):
        masked = {
            "HTML": b"<!DOCTYPE html><html><body>Service unavailable</body></html>",
            "webMethods": b'{"apiResponse": {"type": "E", "message": "Not allowed"}}',
            "FHIR": b'{"resourceType": "OperationOutcome", "issue": [{"severity": "error"}]}',
        }
        for label, body in masked.items():
            with self.subTest(label):
                server = self.serve(Reply(body=body))

                error = self.failure(self.client(server), PlatformError)

                self.assertEqual(error.code, "upstream_unavailable")
                self.assertEqual(error.details, {"surface": "evs", "attempts": 1})
                self.assertEqual(len(server.seen), 1)

    def test_a_response_beyond_the_size_bound_is_not_retried(self):
        server = self.serve(Reply(body=b"x" * 2000))

        error = self.failure(self.client(server), UpstreamTooLargeError)

        self.assertEqual(error.details, {"bound": "BOUND", "limit": 1000, "reached": 2000})
        self.assertEqual(len(server.seen), 1)


class RequestLogTest(ServerTestCase):
    def test_the_hook_receives_one_record_per_attempt(self):
        server = self.serve(Reply(503), Reply(drop=True))
        records = []
        client = self.client(server)
        client.on_request = records.append

        with correlated("call-9"):
            client.get_json("/x", {"q": "free text"})

        self.assertEqual(
            [(r.attempt, r.status, r.failure) for r in records],
            [(1, 503, "http_status"), (2, None, "connection"), (3, 200, None)],
        )
        for record in records:
            self.assertEqual(
                (record.surface, record.method, record.url, record.correlation_id),
                ("evs", "GET", f"{server.url}/x", "call-9"),
            )
            self.assertGreaterEqual(record.elapsed_seconds, 0)

    def test_the_hook_names_what_went_wrong_with_an_attempt_that_got_no_status(self):
        client = HttpClient(
            "http://127.0.0.1:1",
            label="EVS",
            timeout_seconds=7,
            max_attempts=1,
            retry_backoff_seconds=0,
            max_response_bytes=10,
            size_bound="BOUND",
        )
        records = []
        client.on_request = records.append
        failures = {
            "timeout": TimeoutError("timed out"),
            "connection": ConnectionResetError("reset"),
        }
        for failure, raised in failures.items():
            with self.subTest(failure), patch("nci_si_mcp.http_client._open", side_effect=raised):
                with self.assertRaises(UpstreamUnavailableError):
                    client.get_json("/x")

                self.assertEqual((records[-1].status, records[-1].failure), (None, failure))

    def test_a_response_that_is_not_content_is_recorded_with_its_status(self):
        server = self.serve(Reply(body=b"<html>"))
        records = []
        client = self.client(server)
        client.on_request = records.append

        with self.assertRaises(PlatformError):
            client.get_json("/x")

        self.assertEqual([(r.status, r.failure) for r in records], [(200, "unusable_response")])

    def test_there_is_no_hook_unless_the_caller_sets_one(self):
        self.assertIsNone(self.client(self.serve()).on_request)


class CredentialsTest(ServerTestCase):
    def test_the_licence_key_is_sent_on_every_evs_request(self):
        server = self.serve(Reply(503))

        EVSClient(server.url, license_key=KEY).get_api_version()

        self.assertEqual(
            [headers[LICENSE_KEY_HEADER.lower()] for _, headers in server.seen], [KEY] * 2
        )

    def test_a_client_without_credentials_sends_none(self):
        server = self.serve()

        self.client(server).get_json("/x")

        self.assertNotIn(LICENSE_KEY_HEADER.lower(), server.seen[0][1])

    def test_a_redirect_to_another_host_is_refused_and_carries_no_credential(self):
        elsewhere = self.serve()
        server = self.serve(Reply(302, headers={"Location": f"{elsewhere.url}/x"}))
        client = self.client(server, credentials={LICENSE_KEY_HEADER: KEY})

        error = self.failure(client, UpstreamRejectedError)

        self.assertEqual(elsewhere.seen, [])
        self.assertEqual((error.details["status"], len(server.seen)), (302, 1))
        self.assertIn("another origin", str(error))

    def test_a_redirect_to_another_scheme_is_refused_too(self):
        server = self.serve(Reply(301, headers={"Location": "https://127.0.0.1/x"}))

        error = self.failure(self.client(server), UpstreamRejectedError)

        self.assertEqual(error.details["status"], 301)

    def test_a_redirect_within_the_origin_is_followed(self):
        server = self.serve(Reply(302, headers={"Location": "/y"}))
        client = self.client(server, credentials={LICENSE_KEY_HEADER: KEY})

        self.assertEqual(client.get_json("/x"), {"ok": True})

        self.assertEqual([path for path, _ in server.seen], ["/x", "/y"])
        self.assertEqual(server.seen[1][1][LICENSE_KEY_HEADER.lower()], KEY)


class CredentialsStayOutTest(ServerTestCase):
    """The key is echoed by the platform in the body of each kind of failure."""

    ECHOES: ClassVar = {
        "an error status": Reply(403, json.dumps({"message": f"refused {KEY}"}).encode()),
        "a server error": Reply(503, json.dumps({"message": f"busy {KEY}"}).encode()),
        "a webMethods envelope": Reply(
            body=json.dumps({"apiResponse": {"type": "E", "message": f"bad {KEY}"}}).encode()
        ),
    }

    def run_all(self):
        """Run a client with the key against each echo; return what it did and what it said."""

        outputs, records = [], []
        for label, reply in self.ECHOES.items():
            server = self.serve(reply)
            client = EVSClient(server.url, max_attempts=1, license_key=KEY)
            client.http.on_request = records.append
            with self.subTest(label):
                try:
                    client.get_api_version()
                except Exception as error:  # noqa: BLE001 - every kind of failure is a case
                    outputs.append((str(error), getattr(error, "details", None)))
        return outputs, records

    def test_the_key_is_in_no_error_message_or_detail(self):
        with self.assertLogs("nci_si_mcp", level="DEBUG"):
            outputs, _ = self.run_all()

        self.assertEqual(len(outputs), len(self.ECHOES))
        self.assertNotIn(KEY, repr(outputs))
        self.assertIn("[redacted]", repr(outputs))

    def test_the_key_is_in_no_log_line_and_no_hook_record(self):
        with self.assertLogs("nci_si_mcp", level="DEBUG") as logs:
            _, records = self.run_all()

        self.assertEqual(len(records), len(self.ECHOES))
        self.assertNotIn(KEY, repr(records))
        self.assertNotIn(KEY, "\n".join(logs.output))

    def test_the_key_does_not_show_in_the_error_of_the_service(self):
        server = self.serve(self.ECHOES["an error status"])
        client = EVSClient(server.url, license_key=KEY)

        with self.assertRaises(EVSResponseError) as raised:
            client.get_api_version()

        self.assertEqual(raised.exception.details["status"], 403)
        self.assertNotIn(KEY, f"{raised.exception} {raised.exception.details}")


if __name__ == "__main__":
    unittest.main()
