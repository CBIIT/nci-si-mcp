"""Benchmark imports identify invalid fields without disclosing their values."""

import unittest
from copy import deepcopy

from scripts.evidence_envelope import EvidenceError

from test_evidence_benchmark import benchmark, project, selected_projection
from test_evidence_http import http_report, project_http


class BenchmarkRecordTest(unittest.TestCase):
    def test_invalid_selection_names_the_fingerprint_dimension(self):
        with self.assertRaises(EvidenceError) as caught:
            selected_projection(
                change_selection=lambda manifest: manifest["fingerprint"].update(
                    mode="PRIVATE-CANARY"
                )
            )
        self.assertEqual(
            str(caught.exception), "Invalid benchmark field: selection.fingerprint.mode"
        )
        self.assertNotIn("PRIVATE-CANARY", str(caught.exception))

    def test_invalid_report_names_its_field_without_exposing_private_input(self):
        for build, import_report, path, value in (
            (benchmark, project, ("repetitions",), "PRIVATE-CANARY"),
            (benchmark, project, ("suite", "digest"), "PRIVATE-CANARY"),
            (http_report, project_http, ("conditions", "concurrency"), "PRIVATE-CANARY"),
            (http_report, project_http, ("limits", "timeout"), "PRIVATE-CANARY"),
            (http_report, project_http, ("state",), "PRIVATE-CANARY"),
            (http_report, project_http, ("targetSha256",), "PRIVATE-CANARY"),
        ):
            with self.subTest(transport=build()["transport"], path=path):
                report = deepcopy(build())
                parent = report
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = value
                with self.assertRaises(ValueError) as caught:
                    import_report(report)
                self.assertEqual(
                    str(caught.exception), f"Invalid benchmark field: {'.'.join(path)}"
                )
                self.assertNotIn("PRIVATE-CANARY", str(caught.exception))

    def test_invalid_summary_names_the_contradictory_field(self):
        for build, import_report in ((benchmark, project), (http_report, project_http)):
            with self.subTest(transport=build()["transport"]):
                report = deepcopy(build())
                report["cases"][0]["cold"]["summary"]["calls"] = "PRIVATE-CANARY"
                with self.assertRaises(EvidenceError) as caught:
                    import_report(report)
                self.assertEqual(
                    str(caught.exception), "Invalid benchmark field: cases[0].cold.summary.calls"
                )
                self.assertNotIn("PRIVATE-CANARY", str(caught.exception))

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
