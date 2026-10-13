"""One-field import tables preserve strict records and private diagnostics."""

import hashlib
import json
import unittest
from copy import deepcopy
from types import SimpleNamespace

from scripts.evidence_benchmark import comparable, project_benchmark
from scripts.evidence_envelope import EvidenceError
from scripts.http_measurement import MeasurementError, measure_reply

from test_evidence_benchmark import benchmark, selected_projection
from test_evidence_envelope import envelope
from test_evidence_http import http_report


def import_report(native, *, state="completed", selection=None, **metadata):
    raw = json.dumps(native).encode()
    record = envelope(
        kind="benchmark",
        state=state,
        exit_code=0 if state == "completed" else 1,
        report_sha256=hashlib.sha256(raw).hexdigest(),
        **metadata,
    )
    selected = json.dumps(selection).encode() if selection is not None else None
    if selected is not None:
        record["selection_sha256"] = hashlib.sha256(selected).hexdigest()
    return project_benchmark(json.dumps(record).encode(), raw, selection=selected)


def edited(native, path, value):
    result = deepcopy(native)
    parent = result
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    return result


class BenchmarkValidationTest(unittest.TestCase):
    def assert_rejected(self, native, path, **kwargs):
        with self.assertRaises(EvidenceError) as caught:
            import_report(native, **kwargs)
        self.assertEqual(str(caught.exception), f"Invalid benchmark field: {path}")
        self.assertNotIn("PRIVATE-CANARY", str(caught.exception))

    def test_sample_and_case_fields_are_strict(self):
        for build, field, values in (
            (benchmark, "resultBytes", (1.5, 2.0)),
            (benchmark, "outboundRequests", (1.5, 2.0)),
            (http_report, "httpRequests", (1.5, 2.0, 21)),
            (http_report, "elapsedMs", (True,)),
            (benchmark, "responseCode", ("deadline",)),
            (http_report, "resultBytes", (None,)),
            (http_report, "correlationId", ("a" * 31,)),
            (http_report, "outboundRequests", (3,)),
        ):
            for value in values:
                with self.subTest(transport=build()["transport"], field=field, value=value):
                    self.assert_rejected(
                        edited(build(), ("cases", 0, "cold", "samples", 0, field), value),
                        f"cases[0].cold.samples[0].{field}",
                    )
        for field, value in (("arguments", "PRIVATE-CANARY"), ("scenario", 7)):
            with self.subTest(field=field):
                self.assert_rejected(
                    edited(http_report(), ("cases", 0, field), value), f"cases[0].{field}"
                )

    def test_condition_suite_and_header_fields_are_strict(self):
        for build, path, value in (
            (http_report, ("conditions", "concurrency"), True),
            (http_report, ("conditions", "cold"), ""),
            (http_report, ("conditions", "cold"), "x" * 513),
            (benchmark, ("suite", "version"), 5),
            (http_report, ("schema",), 2),
            (http_report, ("schema",), True),
            (http_report, ("httpRequests",), 21),
            (http_report, ("createdAt",), "not-a-time"),
            (benchmark, ("repetitions",), 10001),
            (benchmark, ("sourceSha256",), "PRIVATE-CANARY"),
            (benchmark, ("conditions",), []),
            (http_report, ("mode",), "other"),
            (http_report, ("transport",), "other"),
            (http_report, ("complete",), 1),
            (http_report, ("cases",), {}),
        ):
            with self.subTest(path=path, value=value):
                self.assert_rejected(edited(build(), path, value), ".".join(path))
        for field in ("createdAt", "serverVersion", "sourceSha256", "python", "platform"):
            with self.subTest(field=field):
                self.assert_rejected(edited(benchmark(), (field,), 5), field)
        native = http_report()
        native["limits"]["PRIVATE-CANARY"] = 1
        self.assert_rejected(native, "limits")
        del native["limits"]["PRIVATE-CANARY"]
        del native["limits"]["timeout"]
        self.assert_rejected(native, "limits.timeout")

    def test_report_inventory_and_termination_are_strict(self):
        native = http_report()
        for changes, path, state in (
            ({"cases": []}, "cases", "completed"),
            ({"cases": native["cases"] * 2}, "cases", "completed"),
            ({"expectedCases": []}, "expectedCases", "completed"),
            ({"state": "failed"}, "complete", "failed"),
            ({"stopReason": "deadline"}, "stopReason", "completed"),
            (
                {"state": "failed", "complete": False, "stopReason": "cancelled"},
                "stopReason",
                "failed",
            ),
            (
                {"state": "cancelled", "complete": False, "stopReason": "deadline"},
                "stopReason",
                "cancelled",
            ),
            (
                {"state": "failed", "complete": False, "stopReason": "deadline"},
                "state",
                "completed",
            ),
            ({"httpRequests": 2}, "httpRequests", "completed"),
        ):
            with self.subTest(changes=changes, state=state):
                self.assert_rejected(native | changes, path, state=state)
        self.assert_rejected(edited(benchmark(), ("expectedCases",), ["other"]), "cases")
        phase = native["cases"][0]["cold"]
        self.assert_rejected(
            edited(native, ("cases", 0, "cold", "samples"), phase["samples"] * 2),
            "cases[0].cold.samples",
        )
        two = deepcopy(native["cases"] * 2)
        two[1] = deepcopy(two[1])
        two[1]["tool"] = "get_form"
        self.assert_rejected(
            native | {"expectedCases": ["get_form", "get_concept"], "cases": two}, "cases"
        )

    def test_boundary_values_remain_accepted(self):
        native = http_report()
        native["conditions"]["cold"] = "x" * 512
        native["expectedCases"] = ["a" * 100]
        native["cases"][0]["tool"] = "a" * 100
        native["httpRequests"] = 20
        for phase in native["cases"][0].values():
            if isinstance(phase, dict) and "samples" in phase:
                phase["samples"][0]["httpRequests"] = 0
        native["cases"][0]["cold"]["samples"][0]["httpRequests"] = 20
        self.assertTrue(import_report(native)["inventory_complete"])
        self.assert_rejected(edited(native, ("cases", 0, "tool"), "a" * 101), "cases[0].tool")
        running = http_report() | {"state": "running", "complete": False}
        self.assertFalse(import_report(running, state="interrupted")["inventory_complete"])
        self.assertTrue(import_report(http_report() | {"httpRequests": 3})["inventory_complete"])
        partial = benchmark() | {"repetitions": 10000, "complete": False, "cases": []}
        self.assertEqual(import_report(partial, state="interrupted")["missing"], ["get_concept"])

    def test_selection_case_order_is_bound_to_the_original_inventory(self):
        native = http_report()
        second = deepcopy(native["cases"][0])
        second["tool"] = "get_form"
        for index, name in enumerate(("cold", "warm", "warmups"), 4):
            second[name]["samples"][0]["correlationId"] = str(index) * 32
        native["cases"].append(second)
        native.update(expectedCases=["get_concept", "get_form"], httpRequests=6)
        manifest = {
            "schema": 1,
            "cases": [
                {key: row[key] for key in ("tool", "arguments", "scenario")}
                for row in native["cases"]
            ],
            "fingerprint": import_report(native)["fingerprint"],
        }
        self.assertTrue(import_report(native, selection=manifest)["selection_verified"])
        manifest["cases"].reverse()
        self.assert_rejected(native, "selection.cases", selection=manifest)

    def test_selection_binding_rejects_schema_workload_and_definition_changes(self):
        native = http_report()
        manifest = {
            "schema": 1,
            "cases": [{key: native["cases"][0][key] for key in ("tool", "arguments", "scenario")}],
            "fingerprint": import_report(native)["fingerprint"],
        }
        for path, value, expected in (
            (("schema",), 2, "selection.schema"),
            (("cases", 0, "arguments"), {"code": "other"}, "selection.cases[0]"),
            (("cases",), [], "selection.cases"),
            (("fingerprint", "mode"), "zz", "selection.fingerprint.mode"),
        ):
            with self.subTest(path=path):
                self.assert_rejected(native, expected, selection=edited(manifest, path, value))
        self.assert_rejected(
            edited(native, ("conditions", "cold"), "PRIVATE-CANARY"),
            "selection.fingerprint.definitions",
            selection=manifest,
        )
        for count in (0, 2):
            changed = edited(native, ("limits", "warmups"), count)
            changed["cases"][0]["warmups"] = {
                "samples": [],
                "summary": {
                    "calls": 0,
                    "p50Ms": None,
                    "p95Ms": None,
                    "errorRate": None,
                    "meanResultBytes": None,
                    "meanOutboundRequests": None,
                },
            }
            changed.update(state="failed", complete=False, stopReason="deadline")
            with self.subTest(warmups=count):
                self.assert_rejected(
                    changed, "selection.fingerprint.warmup", selection=manifest, state="failed"
                )

    def test_projection_and_comparison_require_original_binding(self):
        known = selected_projection()
        unverified = known | {"selection_verified": False}
        for left, right in ((known, unverified), (unverified, known)):
            self.assertEqual(comparable(left, right), (False, ["unverified-selection"]))
        unknown = known | {"fingerprint": known["fingerprint"] | {"hardware": None}}
        self.assertEqual(comparable(unknown, known), (False, ["hardware"]))
        raw = json.dumps(http_report()).encode()
        record = envelope(kind="acceptance", report_sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaises(EvidenceError) as caught:
            project_benchmark(json.dumps(record).encode(), raw)
        self.assertEqual(str(caught.exception), "Invalid benchmark field: kind")
        record["kind"] = "benchmark"
        with self.assertRaises(EvidenceError) as caught:
            project_benchmark(json.dumps(record).encode(), raw, selection=b'"PRIVATE-CANARY"')
        self.assertEqual(str(caught.exception), "Invalid benchmark field: selection_sha256")
        self.assertNotIn("PRIVATE-CANARY", str(caught.exception))

    def test_non_object_provenance_has_a_safe_measurement_error(self):
        reply = SimpleNamespace(structured_content={"provenance": "PRIVATE-CANARY"}, is_error=False)
        with self.assertRaises(MeasurementError) as caught:
            measure_reply(reply, 1, "correlation", http_requests=1)
        self.assertEqual(
            str(caught.exception), "Provenance correlation does not match the measured call"
        )
        self.assertNotIn("PRIVATE-CANARY", str(caught.exception))
