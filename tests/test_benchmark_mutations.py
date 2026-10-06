"""Benchmark reports require recorded responses and their longer read timeout."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import benchmark

from nci_si_acceptance import client


class BenchmarkBoundaryTest(unittest.TestCase):
    def test_unrecorded_fixture_response_cannot_be_published_as_complete_evidence(self):
        case = benchmark.cases(["get_concept"])[0]
        case["arguments"]["code"] = "C999999999"
        with TemporaryDirectory() as directory:
            output = Path(directory) / "benchmark.json"
            with (
                patch.object(benchmark, "cases", return_value=[case]),
                self.assertRaisesRegex(ValueError, "unrecorded fixture response"),
            ):
                benchmark.run("fixture", ["get_concept"], 1, output)
            self.assertFalse(json.loads(output.read_text())["complete"])

    def test_benchmark_session_uses_its_own_timeout_instead_of_the_harness_default(self):
        with (
            TemporaryDirectory() as directory,
            patch.object(client, "READ_TIMEOUT_SECONDS", 0.000001),
        ):
            report = benchmark.run("fixture", ["get_concept"], 1, Path(directory) / "report.json")
        self.assertTrue(report["complete"])
        result = report["cases"][0]
        self.assertEqual(result["cold"]["summary"]["errorRate"], 0)
        self.assertEqual(result["warm"]["summary"]["errorRate"], 0)
