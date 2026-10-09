"""HTTP result measurements distinguish observable facts from missing server telemetry."""

import json
import unittest
from types import SimpleNamespace

from scripts.http_measurement import MeasurementError, failure_sample, measure_reply, summarize_http


class HTTPMeasurementTest(unittest.TestCase):
    def reply(self, content, *, error=False):
        return SimpleNamespace(structured_content=content, is_error=error)

    def test_success_measures_utf8_bytes_without_inventing_server_telemetry(self):
        content = {"preferred_name": "Café", "provenance": {"correlationId": "request"}}
        sample = measure_reply(self.reply(content), 12.5, "request", http_requests=2)
        self.assertEqual(
            sample["resultBytes"],
            len(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode()),
        )
        self.assertEqual(sample["httpRequests"], 2)
        self.assertEqual(sample["elapsedMs"], 12.5)
        self.assertFalse(sample["error"])
        for field in ("outboundRequests", "cacheState", "serverCommit", "replica"):
            self.assertIsNone(sample[field])
        self.assertNotIn("Café", json.dumps(sample))

    def test_structured_tool_error_keeps_code_without_retaining_upstream_text(self):
        content = {
            "error": {
                "code": "upstream_unavailable",
                "correlationId": "request",
                "message": "PRIVATE-CANARY",
            }
        }
        sample = measure_reply(self.reply(content, error=True), 50, "request", http_requests=1)
        self.assertTrue(sample["error"])
        self.assertEqual(sample["responseCode"], "upstream_unavailable")
        self.assertNotIn("PRIVATE-CANARY", json.dumps(sample))

    def test_missing_content_protocol_disagreement_and_wrong_correlation_are_rejected(self):
        replies = (
            self.reply(None),
            self.reply({"error": {}}, error=False),
            self.reply({}, error=True),
            self.reply({"provenance": {"correlationId": "another"}}),
            self.reply({"error": {"code": "unknown", "correlationId": "request"}}, error=True),
            self.reply({"error": {"code": "not_found", "correlationId": "another"}}, error=True),
        )
        for reply in replies:
            with self.subTest(reply=reply), self.assertRaises(MeasurementError):
                measure_reply(reply, 1, "request", http_requests=1)

    def test_summary_includes_errors_and_missing_samples_stay_unknown(self):
        good = measure_reply(self.reply({}), 10, "request", http_requests=1)
        bad = good | {"elapsedMs": 100, "error": True, "responseCode": "timeout"}
        summary = summarize_http([good, bad])
        self.assertEqual(summary["calls"], 2)
        self.assertEqual(summary["errorRate"], 0.5)
        self.assertEqual((summary["p50Ms"], summary["p95Ms"]), (10, 100))
        self.assertIsNone(summary["meanOutboundRequests"])
        empty = summarize_http([])
        self.assertEqual(empty["calls"], 0)
        self.assertIsNone(empty["p95Ms"])
        self.assertIsNone(empty["errorRate"])

    def test_client_failure_keeps_latency_and_error_without_claiming_a_result_size(self):
        sample = failure_sample("timeout", 150, "request", http_requests=1)
        summary = summarize_http([sample])
        self.assertEqual(sample["responseCode"], "timeout")
        self.assertTrue(sample["error"])
        self.assertIsNone(sample["resultBytes"])
        self.assertEqual(summary["errorRate"], 1)
        self.assertEqual(summary["p95Ms"], 150)
        self.assertIsNone(summary["meanResultBytes"])
