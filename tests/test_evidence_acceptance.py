"""Recorded acceptance evidence stays tied to its original selected inventory."""

import hashlib
import json
import unittest
from copy import deepcopy

from scripts.evidence_acceptance import project_acceptance
from scripts.evidence_envelope import EvidenceError

from test_evidence_envelope import envelope


def encoded(value):
    return json.dumps(value).encode()


def bundle():
    cases = {
        "tests/test_example.py::test_lookup": {"tool": "lookup", "gate": False},
        "tests/test_example.py::test_protocol": {"tool": None, "gate": True},
    }
    return {
        "catalogue": encoded(
            {
                "schema": 1,
                "suite_digest": "a" * 64,
                "tools": {"lookup": "evs"},
                "cases": cases,
            }
        ),
        "stories": encoded(dict.fromkeys(cases, "find-a-concept")),
        "expectations": encoded(dict.fromkeys(cases, "passed")),
        "selection": encoded(list(cases)),
    }


def report():
    return {
        "mode": "fixture",
        "transport": "stdio",
        "suite": {"version": "1.0", "fixture_set": "recorded", "digest": "a" * 64},
        "failed_gates": [],
        "unrun_gates": [],
        "tools_list_bytes": 100,
        "tools": {
            "lookup": {
                "group": "evs",
                "outcome": "PASS",
                "gates_only": False,
                "counts": {"passed": 1},
            }
        },
        "tests": {
            key: row | {"outcome": "passed", "unmatched": []}
            for key, row in json.loads(bundle()["catalogue"])["cases"].items()
        },
    }


def project(native=None, context=None, **changes):
    context = bundle() if context is None else context
    native = report() if native is None else native
    raw = encoded(native)
    metadata = envelope(report_sha256=hashlib.sha256(raw).hexdigest(), **changes)
    metadata.update(
        {name + "_sha256": hashlib.sha256(data).hexdigest() for name, data in context.items()}
    )
    return project_acceptance(encoded(metadata), raw, **context)


class AcceptanceProjectionTest(unittest.TestCase):
    def test_a_report_without_the_alias_field_keeps_its_verdict(self):
        native = report()
        self.assertNotIn("implemented_as", native["tools"]["lookup"])
        self.assertEqual(project(native)["tools"]["lookup"]["outcome"], "PASS")
        native["tools"]["lookup"]["outcome"] = "FAIL"
        with self.assertRaises(EvidenceError):
            project(native)

    def test_a_not_implemented_verdict_needs_no_alias_field(self):
        native = report()
        native["tools"]["lookup"]["outcome"] = "NOT IMPLEMENTED"
        self.assertEqual(project(native)["tools"]["lookup"]["outcome"], "NOT IMPLEMENTED")

    def test_a_recorded_report_with_the_alias_field_stays_valid_and_checked(self):
        native = report()
        native["tools"]["lookup"]["implemented_as"] = "lookup"
        self.assertEqual(project(native)["tools"]["lookup"]["outcome"], "PASS")
        native["tools"]["lookup"].update(implemented_as=None, outcome="NOT IMPLEMENTED")
        self.assertEqual(project(native)["tools"]["lookup"]["outcome"], "NOT IMPLEMENTED")
        native["tools"]["lookup"].update(implemented_as="lookup", outcome="NOT IMPLEMENTED")
        with self.assertRaises(EvidenceError):
            project(native)

    def test_unrun_gate_and_test_remain_unrun_and_incomplete(self):
        native = report()
        key = "tests/test_example.py::test_protocol"
        native["tests"][key]["outcome"] = "skipped"
        native["unrun_gates"] = [key]
        result = project(native)
        self.assertEqual(result["counts"], {"passed": 1, "skipped": 1})
        self.assertTrue(result["inventory_complete"])
        self.assertEqual(result["tools"]["lookup"]["outcome"], "PASS")

    def test_malformed_verdict_is_a_safe_validation_error(self):
        native = report()
        native["tools"]["lookup"]["outcome"] = ["PRIVATE-CANARY"]
        with self.assertRaises(EvidenceError) as failure:
            project(native)
        self.assertNotIn("PRIVATE-CANARY", str(failure.exception))

    def test_projection_preserves_verdicts_and_original_story_and_expected_outcome(self):
        result = project()
        self.assertTrue(result["inventory_complete"])
        self.assertEqual(result["tools"]["lookup"]["outcome"], "PASS")
        self.assertEqual(result["counts"], {"passed": 2})
        self.assertEqual(result["missing"], [])
        self.assertTrue(all(row["story"] == "find-a-concept" for row in result["cases"]))
        self.assertTrue(all(row["expected"] == "passed" for row in result["cases"]))
        self.assertIsNone(result["server_commit"])

    def test_missing_cases_are_not_inferred_passed_from_successful_process_exit(self):
        native = report()
        del native["tests"]["tests/test_example.py::test_protocol"]
        result = project(native)
        self.assertFalse(result["inventory_complete"])
        self.assertEqual(len(result["missing"]), 1)
        self.assertEqual(result["counts"], {"passed": 1})

    def test_interrupted_run_cannot_become_complete_even_with_all_results(self):
        result = project(state="interrupted", exit_code=None)
        self.assertFalse(result["inventory_complete"])
        self.assertEqual(result["state"], "interrupted")

    def test_selection_is_exact_and_can_be_a_subset_of_original_catalogue(self):
        context = bundle()
        context["selection"] = encoded(["tests/test_example.py::test_lookup"])
        native = report()
        del native["tests"]["tests/test_example.py::test_protocol"]
        self.assertTrue(project(native, context)["inventory_complete"])
        with self.assertRaises(EvidenceError):
            project(context=context)

    def test_raw_urls_labels_and_unmatched_credentials_never_reach_projection(self):
        native = report()
        native["suite"]["version"] = "<script>SECRET-CANARY</script>"
        native["tests"]["tests/test_example.py::test_lookup"]["unmatched"] = [
            "https://secret.invalid/?credential=SECRET-CANARY"
        ]
        result = project(native)
        self.assertNotIn("SECRET-CANARY", json.dumps(result))
        self.assertNotIn("test_example.py", json.dumps(result))

    def test_report_cannot_reassign_a_gate_or_tool(self):
        for field, value in (("gate", False), ("tool", "lookup")):
            native = report()
            native["tests"]["tests/test_example.py::test_protocol"][field] = value
            with self.subTest(field=field), self.assertRaises(EvidenceError):
                project(native)

    def test_counts_gates_and_verdicts_must_agree_with_individual_results(self):
        for field, value in (
            ("counts", {"passed": 2}),
            ("outcome", "FAIL"),
            ("gates_only", True),
            ("group", "cadsr"),
        ):
            native = report()
            native["tools"]["lookup"][field] = value
            with self.subTest(field=field), self.assertRaises(EvidenceError):
                project(native)

    def test_gate_failure_uses_harness_verdict_without_reinterpreting_no_fixture(self):
        for outcome in ("failed", "no_fixture"):
            native = report()
            key = "tests/test_example.py::test_protocol"
            native["tests"][key]["outcome"] = outcome
            native["failed_gates"] = [key]
            native["tools"]["lookup"].update(outcome="FAIL", gates_only=True)
            with self.subTest(outcome=outcome):
                result = project(native, state="failed", exit_code=1)
                self.assertEqual(result["tools"]["lookup"]["outcome"], "FAIL")
                self.assertEqual(result["counts"][outcome], 1)

    def test_original_suite_must_match_catalogue_not_current_checkout(self):
        native = report()
        native["suite"]["digest"] = "b" * 64
        with self.assertRaises(EvidenceError):
            project(native)

    def test_replacing_any_context_bytes_breaks_binding(self):
        context = bundle()
        raw = encoded(report())
        metadata = envelope(report_sha256=hashlib.sha256(raw).hexdigest())
        metadata.update(
            {name + "_sha256": hashlib.sha256(data).hexdigest() for name, data in context.items()}
        )
        for name in context:
            changed = context | {name: context[name] + b" "}
            with self.subTest(name=name), self.assertRaises(EvidenceError):
                project_acceptance(encoded(metadata), raw, **changed)

    def test_missing_report_preserves_execution_without_inventing_test_counts(self):
        context = bundle()
        metadata = envelope(state="unavailable", exit_code=None, report_sha256=None)
        metadata.update(
            {name + "_sha256": hashlib.sha256(data).hexdigest() for name, data in context.items()}
        )
        result = project_acceptance(encoded(metadata), None, **context)
        self.assertIsNone(result["counts"])
        self.assertFalse(result["inventory_complete"])
        self.assertEqual(len(result["missing"]), 2)

    def test_malformed_context_cannot_create_a_different_inventory(self):
        for name, data in (
            ("selection", ["unknown"]),
            ("selection", ["tests/test_example.py::test_lookup"] * 2),
            ("stories", {}),
            ("expectations", {}),
            ("catalogue", []),
        ):
            with self.subTest(name=name), self.assertRaises(EvidenceError):
                project(context=bundle() | {name: encoded(data)})

    def test_native_schema_rejects_malformed_types_and_unknown_statuses(self):
        for field, value in (
            ("mode", "other"),
            ("transport", "file"),
            ("tools_list_bytes", True),
            ("tests", []),
            ("failed_gates", ["invented"]),
        ):
            native = report() | {field: value}
            with self.subTest(field=field), self.assertRaises(EvidenceError):
                project(native)

    def test_unknown_case_fields_and_boolean_counts_fail_closed(self):
        originals = []
        native = report()
        native["tools"]["lookup"]["counts"] = {"passed": True}
        originals.append(native)
        native = report()
        native["tests"]["tests/test_example.py::test_lookup"]["unexpected"] = "canary"
        originals.append(native)
        for native in originals:
            with self.subTest(native=native), self.assertRaises(EvidenceError):
                project(deepcopy(native))
