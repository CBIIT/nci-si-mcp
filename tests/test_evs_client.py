import io
import unittest
from http.client import IncompleteRead
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from nci_si_mcp.evs import (
    EVSClient,
    EVSNotFoundError,
    EVSResponseError,
    EVSResponseTooLargeError,
    EVSUnavailableError,
)


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload[:limit]


def http_error(status, body=b""):
    return HTTPError("https://example.invalid", status, "Reason", {}, io.BytesIO(body))


@patch("nci_si_mcp.evs.time.sleep")
@patch("nci_si_mcp.evs.urlopen")
class EVSClientTest(unittest.TestCase):
    def client(self, **options):
        return EVSClient("https://example.invalid/", **options)

    def test_transient_failures_are_retried_until_one_succeeds(self, urlopen, sleep):
        failures = {
            "network": URLError("temporary"),
            "timeout": TimeoutError("timed out"),
            "reset": ConnectionResetError("reset"),
            "http 429": http_error(429),
            "http 500": http_error(500),
            "http 503": http_error(503),
            "truncated body": FakeResponse(IncompleteRead(b"")),
        }
        for label, failure in failures.items():
            with self.subTest(label):
                urlopen.reset_mock()
                urlopen.side_effect = [failure, FakeResponse(b'{"version": "test"}')]

                with self.assertLogs("nci_si_mcp.evs", level="WARNING"):
                    result = self.client(max_attempts=2).get_api_version()

                self.assertEqual(result, {"version": "test"})
                self.assertEqual(urlopen.call_count, 2)

    def test_exhausted_attempts_raise_unavailable_after_exponential_backoff(self, urlopen, sleep):
        urlopen.side_effect = [URLError("down"), http_error(503), URLError("still down")]

        with self.assertLogs("nci_si_mcp.evs", level="WARNING"), self.assertRaises(
            EVSUnavailableError
        ) as raised:
            self.client(max_attempts=3, retry_backoff_seconds=0.25).get_api_version()

        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.25, 0.5])
        self.assertIn("still down", str(raised.exception))

    def test_not_found_is_distinct_and_carries_the_reason_evs_gives(self, urlopen, sleep):
        urlopen.side_effect = http_error(404, b'{"status": 404, "message": "C999 not found"}')

        with self.assertRaises(EVSNotFoundError) as raised:
            self.client().get_concept("C999")

        self.assertEqual(urlopen.call_count, 1)
        self.assertIn("HTTP 404", str(raised.exception))
        self.assertIn("C999 not found", str(raised.exception))

    def test_other_client_errors_are_not_retried(self, urlopen, sleep):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                urlopen.reset_mock()
                urlopen.side_effect = http_error(status, b"<html>")

                with self.assertRaises(EVSResponseError) as raised:
                    self.client().get_api_version()

                self.assertNotIsInstance(raised.exception, EVSNotFoundError)
                self.assertEqual(urlopen.call_count, 1)

    def test_response_size_is_bounded_before_and_while_reading(self, urlopen, sleep):
        class UnreadableResponse(FakeResponse):
            def read(self, limit):
                raise AssertionError("an oversized response must not be read")

        client = self.client(max_response_bytes=10)
        responses = {
            "declared": UnreadableResponse(b"", {"Content-Length": "11"}),
            "undeclared": FakeResponse(b'{"a": 12345}'),
        }
        for label, response in responses.items():
            with self.subTest(label):
                urlopen.reset_mock()
                urlopen.side_effect = None
                urlopen.return_value = response

                with self.assertRaises(EVSResponseTooLargeError):
                    client.get_api_version()

                self.assertEqual(urlopen.call_count, 1)

        urlopen.return_value = FakeResponse(b'{"a": 123}')
        self.assertEqual(client.get_api_version(), {"a": 123})

    def test_unusable_bodies_raise_response_errors(self, urlopen, sleep):
        for body in (b"<html>", b"\xff"):
            with self.subTest(body=body):
                urlopen.return_value = FakeResponse(body)
                with self.assertRaises(EVSResponseError):
                    self.client().get_api_version()

    def test_unexpected_shapes_raise_response_errors(self, urlopen, sleep):
        client = self.client()
        calls = {
            "version as list": (b"[]", client.get_api_version),
            "terminologies as object": (b"{}", client.get_terminologies),
            "terminologies of strings": (b'["ncit"]', client.get_terminologies),
            "concept as list": (b"[]", lambda: client.get_concept("C1")),
            "concept list as object": (b"{}", lambda: client.get_concepts_by_codes(["C1"])),
            "descendants as object": (b"{}", lambda: client.get_descendants("C1", 1)),
        }
        for label, (body, call) in calls.items():
            with self.subTest(label):
                urlopen.return_value = FakeResponse(body)
                with self.assertRaises(EVSResponseError):
                    call()

    def test_requests_address_the_given_terminology(self, urlopen, sleep):
        client = self.client()
        calls = {
            "/api/v1/concept/ncit_26.06e/C1?include=minimal": (
                b"{}",
                lambda: client.get_concept("C1", terminology="ncit_26.06e", include="minimal"),
            ),
            "/api/v1/concept/ncit_26.06e?list=C1%2CC2&include=minimal": (
                b"[]",
                lambda: client.get_concepts_by_codes(
                    ["C1", " C2 ", ""], terminology="ncit_26.06e", include="minimal"
                ),
            ),
            "/api/v1/concept/ncit_26.06e/C1/descendants?maxLevel=2": (
                b"[]",
                lambda: client.get_descendants("C1", 2, terminology="ncit_26.06e"),
            ),
            "/api/v1/concept/ncit/C1/descendants?maxLevel=1": (
                b"[]",
                lambda: client.get_descendants("C1", 1),
            ),
        }
        for path, (body, call) in calls.items():
            with self.subTest(path):
                urlopen.return_value = FakeResponse(body)
                call()
                request = urlopen.call_args.args[0]
                self.assertEqual(request.full_url, f"https://example.invalid{path}")
                self.assertEqual(request.get_header("Accept"), "application/json")

    def test_no_codes_means_no_request(self, urlopen, sleep):
        self.assertEqual(self.client().get_concepts_by_codes([" ", ""]), [])
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
