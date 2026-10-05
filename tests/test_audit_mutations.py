import asyncio
import hashlib
import json
import logging
import unittest
from unittest.mock import patch

from fakes import concept
from nci_si_mcp.audit import audited, emit, hashed, secrets
from nci_si_mcp.evs import EVSClient
from nci_si_mcp.http_client import UpstreamUnavailableError
from test_audit import captured, records
from test_evs_client import FakeResponse, http_error
from test_server import ServerFixture


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


class AuditMutationTest(unittest.TestCase):
    def test_request_and_retry_diagnostics_hash_each_original_value(self):
        client = EVSClient("https://example.invalid", retry_backoff_seconds=0).http
        path = "/private-path-canary"
        reason = f"EVS request failed for {path}: HTTP 503 Reason"
        with (
            captured() as stream,
            patch(
                "nci_si_mcp.http_client._open", side_effect=[http_error(503), FakeResponse(b"{}")]
            ),
        ):
            self.assertEqual(client.get_json(path), {})
        attempts = records(stream, "upstream_request")
        self.assertEqual([row["urlHash"] for row in attempts], [digest(client.base_url + path)] * 2)
        (retry,) = records(stream, "upstream_request_retry")
        self.assertEqual(retry["pathHash"], digest(path))
        self.assertEqual(retry["reasonHash"], digest(reason))
        for value in (client.base_url, path, reason):
            self.assertNotIn(value, stream.getvalue())

    def test_request_observer_receives_a_redacted_url(self):
        client = EVSClient("https://example.invalid", license_key="key-canary").http
        observed = []
        client.on_request = observed.append
        with patch("nci_si_mcp.http_client._open", return_value=FakeResponse(b"{}")):
            result = client.get_json("/key-canary/concept")
        self.assertEqual(result, {})
        self.assertEqual(
            [row.url for row in observed], ["https://example.invalid/[redacted]/concept"]
        )

    def test_rejected_content_reason_redacts_credentials(self):
        client = EVSClient("https://example.invalid", license_key="key-canary").http
        with (
            patch("nci_si_mcp.http_client._open", return_value=FakeResponse(b"{}")),
            self.assertRaises(UpstreamUnavailableError) as raised,
        ):
            client.get_json("/metadata", reject=lambda payload: "unusable key-canary")
        self.assertEqual(str(raised.exception), "unusable [redacted]")
        self.assertEqual(raised.exception.details, {"surface": "evs", "status": 200, "attempts": 1})

    def test_password_alone_is_redacted_from_a_diagnostic(self):
        with captured() as stream, audited("lookup", {}, {}, secrets(None, "user:pw123")):
            emit(logging.getLogger("nci_si_mcp.test"), logging.WARNING, "canary", value="pw123")
        self.assertEqual(records(stream, "canary")[0]["value"], "[redacted]")
        self.assertNotIn("pw123", stream.getvalue())

    def test_hashes_do_not_depend_on_object_key_order(self):
        self.assertEqual(hashed({"a": 1, "b": 2}), hashed({"b": 2, "a": 1}))
        self.assertEqual(hashed({"b": 2, "a": 1}), {"sha256": digest({"a": 1, "b": 2})})

    def test_raw_upstream_release_is_excluded_and_platform_releases_are_deduplicated(self):
        release = {"terminology": "ncit", "identifier": "26.06e"}
        with captured() as stream, audited("lookup", {}, {}, ()) as record:
            record.result = {
                "nodes": [{"provenance": {"release": dict(release)}} for _ in range(3)],
                "raw": {"provenance": {"release": {"identifier": "upstream-canary"}}},
            }
        self.assertEqual(records(stream)[0]["release"]["resolved"], [release])
        self.assertNotIn("upstream-canary", stream.getvalue())

    def test_cancellation_propagates_and_discards_a_previously_set_result(self):
        with (
            captured() as stream,
            self.assertRaises(asyncio.CancelledError),
            audited("lookup", {}, {}, ()) as record,
        ):
            record.result = {"code": "C1"}
            raise asyncio.CancelledError()
        (result,) = records(stream)
        self.assertEqual(result["errorType"], "CancelledError")
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["resultSize"])


class IndexAuditMutationTest(ServerFixture):
    def test_replacing_an_index_logs_the_release_it_replaced(self):
        with captured() as stream:
            for version in ("26.06e", "26.07a"):
                self.context.index.upsert_concepts(
                    [concept("C1", version=version)], None, self.context.embedding_provider
                )
        (record,) = records(stream, "index_release_replaced")
        self.assertEqual((record["previous"], record["release"]), ("26.06e", "26.07a"))
        self.assertEqual(self.context.index.get_active_manifest().release_version, "26.07a")
