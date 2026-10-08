"""HTTP evidence cannot turn missing telemetry, partial calls or inconsistent counts into proof."""

import hashlib
import json
import unittest
from copy import deepcopy

from scripts.evidence_benchmark import project_benchmark
from scripts.http_measurement import summarize_http

from test_evidence_envelope import envelope


def http_report():
    sample = {
        "elapsedMs": 10,
        "resultBytes": 32,
        "httpRequests": 1,
        "responseCode": "ok",
        "error": False,
        "correlationId": "1" * 32,
        "outboundRequests": None,
        "cacheState": None,
        "serverCommit": None,
        "replica": None,
    }
    case = {"tool": "get_concept", "arguments": {"code": "PRIVATE-CANARY"}, "scenario": None}
    for number, phase in enumerate(("cold", "warm", "warmups"), 1):
        rows = [sample | {"correlationId": str(number) * 32}]
        case[phase] = {"samples": rows, "summary": summarize_http(rows)}
    return {
        "schema": 1,
        "transport": "streamable-http",
        "mode": "fixture",
        "createdAt": "2026-10-08T10:00:00Z",
        "state": "completed",
        "complete": True,
        "stopReason": None,
        "httpRequests": 5,
        "limits": {
            "max_requests": 20,
            "seconds": 120,
            "timeout": 15,
            "repetitions": 1,
            "warmups": 1,
        },
        "targetSha256": "a" * 64,
        "expectedCases": ["get_concept"],
        "conditions": {
            "cold": "first call in a new session",
            "warm": "after warm-up",
            "timing": "client observed",
            "concurrency": 1,
            "remoteTermination": "unknown",
            "serverTelemetry": "unknown",
        },
        "cases": [case],
    }


def project_http(report=None, *, selection=None, **metadata):
    raw = json.dumps(http_report() if report is None else report).encode()
    record = envelope(kind="benchmark", report_sha256=hashlib.sha256(raw).hexdigest(), **metadata)
    if selection is not None:
        record["selection_sha256"] = hashlib.sha256(selection).hexdigest()
    return project_benchmark(json.dumps(record).encode(), raw, selection=selection)


class HTTPEvidenceTest(unittest.TestCase):
    def test_client_timeout_counts_as_error_without_a_fabricated_result_size(self):
        report = http_report()
        phase = report["cases"][0]["cold"]
        phase["samples"][0].update(responseCode="timeout", resultBytes=None, error=True)
        phase["summary"] = summarize_http(phase["samples"])
        report.update(complete=False, state="failed", stopReason="timeout")
        result = project_http(report, state="failed", exit_code=1)
        self.assertEqual(result["cases"][0]["cold"]["errors"], 1)
        self.assertIsNone(result["cases"][0]["cold"]["summary"]["meanResultBytes"])
        self.assertFalse(result["inventory_complete"])

    def test_projection_preserves_measured_counts_and_omits_arguments_and_unknown_claims(self):
        result = project_http()
        self.assertEqual(result["cases"][0]["warmups"]["summary"]["calls"], 1)
        self.assertTrue(result["inventory_complete"])
        self.assertFalse(result["comparison_ready"])
        self.assertIsNone(result["fingerprint"]["release"])
        self.assertNotIn("PRIVATE-CANARY", json.dumps(result))

    def test_sum_of_measured_http_requests_cannot_exceed_campaign_total(self):
        report = http_report()
        report["httpRequests"] = 2
        with self.assertRaises(ValueError):
            project_http(report)

    def test_reusing_a_call_correlation_cannot_inflate_the_sample_count(self):
        report = http_report()
        report["cases"][0]["warm"]["samples"][0]["correlationId"] = "1" * 32
        with self.assertRaises(ValueError):
            project_http(report)

    def test_declared_concurrency_cannot_disagree_with_the_supported_profile(self):
        report = http_report()
        report["conditions"]["concurrency"] = 2
        with self.assertRaises(ValueError):
            project_http(report)

    def test_summary_boolean_cannot_impersonate_a_numeric_call_count(self):
        report = http_report()
        report["cases"][0]["cold"]["summary"]["calls"] = True
        with self.assertRaises(ValueError):
            project_http(report)

    def test_missing_warm_phase_cannot_be_marked_complete(self):
        report = http_report()
        report["cases"][0]["warm"] = {"samples": [], "summary": summarize_http([])}
        with self.assertRaises(ValueError):
            project_http(report)
        report.update(state="failed", complete=False, stopReason="timeout")
        result = project_http(report, state="failed", exit_code=1)
        self.assertFalse(result["inventory_complete"])
        self.assertEqual(result["missing"], ["get_concept"])
        self.assertIsNone(result["cases"][0]["warm"]["summary"]["p95Ms"])

    def test_declared_server_telemetry_is_not_accepted_as_observed_http_data(self):
        for field, value in (
            ("outboundRequests", 0),
            ("cacheState", "warm"),
            ("serverCommit", "a" * 40),
            ("replica", "claimed"),
        ):
            report = http_report()
            report["cases"][0]["cold"]["samples"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                project_http(report)

    def test_bound_selection_preserves_original_workload_without_authenticating_it(self):
        report = http_report()
        manifest = {
            "schema": 1,
            "cases": [{key: report["cases"][0][key] for key in ("tool", "arguments", "scenario")}],
            "fingerprint": project_http(report)["fingerprint"],
        }
        result = project_http(report, selection=json.dumps(manifest).encode())
        self.assertTrue(result["selection_verified"])
        self.assertFalse(result["comparison_ready"])
        bad = deepcopy(manifest)
        bad["cases"][0]["arguments"] = {}
        with self.assertRaises(ValueError):
            project_http(report, selection=json.dumps(bad).encode())
