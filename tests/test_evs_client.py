import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from nci_si_mcp.evs import EVSClient, EVSError


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit):
        return self.payload[:limit]


class EVSClientTest(unittest.TestCase):
    @patch("nci_si_mcp.evs.urlopen")
    def test_transient_network_failure_is_retried(self, mocked_urlopen):
        mocked_urlopen.side_effect = [
            URLError("temporary"),
            FakeResponse(b'{"version": "test"}'),
        ]
        client = EVSClient(
            "https://example.invalid",
            max_attempts=2,
            retry_backoff_seconds=0,
        )

        result = client.get_api_version()

        self.assertEqual(result["version"], "test")
        self.assertEqual(mocked_urlopen.call_count, 2)

    @patch("nci_si_mcp.evs.urlopen")
    def test_non_retryable_http_error_fails_immediately(self, mocked_urlopen):
        mocked_urlopen.side_effect = HTTPError(
            "https://example.invalid",
            404,
            "Not Found",
            {},
            io.BytesIO(),
        )
        client = EVSClient("https://example.invalid", max_attempts=3)

        with self.assertRaises(EVSError):
            client.get_api_version()

        self.assertEqual(mocked_urlopen.call_count, 1)

    @patch("nci_si_mcp.evs.urlopen")
    def test_response_size_is_bounded(self, mocked_urlopen):
        mocked_urlopen.return_value = FakeResponse(b'{"payload": "too large"}')
        client = EVSClient("https://example.invalid", max_response_bytes=5)

        with self.assertRaises(EVSError):
            client.get_api_version()


if __name__ == "__main__":
    unittest.main()
