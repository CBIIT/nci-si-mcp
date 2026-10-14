import io
import json
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from scripts import benchmark
from scripts.benchmark_records import percentile, summarize

from nci_si_acceptance import client
from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures


def sample(elapsed=1.0, *, error=False, requests=2, size=9):
    return {
        "elapsedMs": elapsed,
        "error": error,
        "outboundRequests": requests,
        "resultBytes": size,
        "responseCode": "upstream_unavailable" if error else "ok",
        "release": {},
    }


class BenchmarkTest(unittest.TestCase):
    def test_an_interrupted_first_case_cannot_leave_a_stale_successful_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "benchmark.json"
            output.write_text('{"complete": true}')
            with (
                patch.object(benchmark, "run_case", side_effect=ValueError("missing audit")),
                self.assertRaisesRegex(ValueError, "missing audit"),
            ):
                benchmark.run("live", ["get_form"], 1, output)
            report = json.loads(output.read_text())
        self.assertFalse(report["complete"])
        self.assertEqual(report["cases"], [])
        self.assertEqual(report["expectedCases"], ["get_form"])

    def test_a_representative_call_produces_successful_correlated_fixture_evidence(self):
        # match_data_elements needs its fixture scenario (cadsr/credentialed) activated.
        names = ["get_concept", "match_data_elements"]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "benchmark.json"
            report = benchmark.run("fixture", names, 1, output)
            self.assertEqual(json.loads(output.read_text()), report)
        self.assertTrue(report["complete"])
        self.assertEqual(report["expectedCases"], names)
        self.assertEqual([case["tool"] for case in report["cases"]], names)
        for case in report["cases"]:
            with self.subTest(tool=case["tool"]):
                self.assertEqual(case["cold"]["summary"]["errorRate"], 0)
                self.assertEqual(case["warm"]["summary"]["errorRate"], 0)
                self.assertEqual(len(case["cold"]["samples"]), 1)
                self.assertEqual(len(case["warm"]["samples"]), 1)
                self.assertGreater(case["warm"]["summary"]["meanResultBytes"], 0)
                self.assertGreater(case["cold"]["summary"]["meanOutboundRequests"], 0)

    def test_nearest_rank_and_error_calls_remain_in_the_distribution(self):
        maximum = 20
        rows = [
            sample(float(value), error=value == maximum, requests=value) for value in range(1, 21)
        ]
        result = summarize(rows)
        self.assertEqual((result["p50Ms"], result["p95Ms"]), (10, 19))
        self.assertEqual(result["errorRate"], 0.05)
        self.assertEqual(result["meanOutboundRequests"], 10.5)
        self.assertEqual(result["meanResultBytes"], 9)
        self.assertEqual(result["calls"], 20)

    def test_invalid_timing_evidence_is_refused(self):
        for values, fraction in (([], 0.5), ([1], 0), ([1], 1.1), ([float("nan")], 0.5), ([-1], 1)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                percentile(values, fraction)

    def test_only_one_correlated_completion_with_actual_attempts_is_evidence(self):
        record = {
            "event": "call_completed",
            "correlationId": "call",
            "tool": "get_concept",
            "outboundRequests": 3,
        }
        line = json.dumps(record)
        other = json.dumps(record | {"correlationId": "another", "outboundRequests": 99})
        self.assertEqual(
            benchmark.completion(other + "\n" + line, "call", "get_concept")["outboundRequests"], 3
        )
        bad = ["", line + "\n" + line, json.dumps(record | {"tool": "another"})]
        bad += [json.dumps(record | {"outboundRequests": value}) for value in (None, True, -1)]
        for log in bad:
            with self.subTest(log=log), self.assertRaises(ValueError):
                benchmark.completion(log, "call", "get_concept")

    def test_measure_uses_utf8_result_and_correlated_attempts_including_error(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "audit.jsonl"
            content = {"error": {"code": "upstream_unavailable", "message": "é"}}

            def call_tool(name, _arguments, *, meta):
                record = {
                    "event": "call_completed",
                    "correlationId": meta["correlationId"],
                    "tool": name,
                    "outboundRequests": 3,
                    "status": "error",
                    "responseCode": "upstream_unavailable",
                    "release": {"requested": "26.09d", "resolved": []},
                }
                log.write_text(json.dumps(record) + "\n")
                return SimpleNamespace(structured_content=content, is_error=True)

            measured = benchmark.measure(
                SimpleNamespace(call_tool=call_tool), log, {"tool": "get_concept", "arguments": {}}
            )
        self.assertTrue(measured["error"])
        self.assertEqual(measured["outboundRequests"], 3)
        self.assertEqual(measured["release"]["requested"], "26.09d")
        expected = b'{"error":{"code":"upstream_unavailable","message":"\xc3\xa9"}}'
        self.assertEqual(measured["resultBytes"], len(expected))

    def test_cold_processes_are_distinct_and_warm_priming_is_excluded(self):
        processes = []

        @contextmanager
        def opened(_environment):
            process = {"id": len(processes), "calls": 0}
            processes.append(process)
            yield process, None

        def measure(process, _log, _case):
            process["calls"] += 1
            return sample(100 if process["calls"] == 1 else 5, requests=process["calls"])

        with patch.object(benchmark, "opened", opened), patch.object(benchmark, "measure", measure):
            result = benchmark.run_case(
                {"tool": "get_concept", "arguments": {}, "scenario": None}, {}, 2
            )
        self.assertEqual([process["calls"] for process in processes], [1, 1, 3])
        self.assertEqual(result["cold"]["summary"]["p50Ms"], 100)
        self.assertEqual(result["warm"]["summary"]["p95Ms"], 5)
        self.assertEqual(result["warm"]["summary"]["meanOutboundRequests"], 2.5)

    def test_missing_content_or_disagreeing_mcp_error_does_not_become_a_measurement(self):
        replies = [
            SimpleNamespace(structured_content=None, is_error=True),
            SimpleNamespace(structured_content={"answer": []}, is_error=False),
        ]
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "audit.jsonl"
            log.write_text("")
            for reply in replies:
                with (
                    self.subTest(reply=reply),
                    patch.object(benchmark, "completion", return_value={"status": "error"}),
                    self.assertRaisesRegex(ValueError, "disagree"),
                ):
                    session = SimpleNamespace(
                        call_tool=lambda *_args, reply=reply, **_kwargs: reply
                    )
                    benchmark.measure(session, log, {"tool": "get_concept", "arguments": {}})


class BenchmarkCommandTest(unittest.TestCase):
    def test_nonpositive_repetitions_are_rejected_without_creating_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            for repetitions in ("0", "-1"):
                with (
                    self.subTest(repetitions=repetitions),
                    patch(
                        "sys.argv",
                        ["benchmark", "--output", str(output), "--repetitions", repetitions],
                    ),
                    redirect_stdout(io.StringIO()),
                    self.assertRaisesRegex(ValueError, "At least one measured repetition"),
                ):
                    benchmark.main()
                self.assertFalse(output.exists())


class CaseIsolationTest(unittest.TestCase):
    def test_a_case_starts_with_the_log_of_no_earlier_request(self):
        case = benchmark.cases(["get_concept"])[0]
        with FixtureServer(load_fixtures(benchmark.FIXTURES)) as upstream:
            connection = HTTPConnection(urlsplit(upstream.url).netloc, timeout=3)
            self.addCleanup(connection.close)
            connection.request("GET", "/left-by-the-previous-case")
            connection.getresponse().read()
            self.assertEqual(len(upstream.log()), 1)
            benchmark.environment_for("fixture", upstream, case)
            self.assertEqual(upstream.log(), [])


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
