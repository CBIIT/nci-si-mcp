"""Benchmark imports identify invalid fields without disclosing their values."""

import unittest
from copy import deepcopy

from scripts.evidence_envelope import EvidenceError

from test_evidence_benchmark import benchmark, project
from test_evidence_http import http_report, project_http


class BenchmarkRecordTest(unittest.TestCase):
    def test_invalid_sample_names_its_field_without_exposing_the_value(self):
        for build, import_report, field in (
            (benchmark, project, "release"),
            (benchmark, project, "outboundRequests"),
            (http_report, project_http, "correlationId"),
            (http_report, project_http, "cacheState"),
            (http_report, project_http, "responseCode"),
        ):
            with self.subTest(transport=build()["transport"], field=field):
                report = deepcopy(build())
                report["cases"][0]["cold"]["samples"][0][field] = "PRIVATE-CANARY"
                with self.assertRaises(EvidenceError) as caught:
                    import_report(report)
                self.assertEqual(
                    str(caught.exception),
                    f"Invalid benchmark field: cases[0].cold.samples[0].{field}",
                )
                self.assertNotIn("PRIVATE-CANARY", str(caught.exception))
