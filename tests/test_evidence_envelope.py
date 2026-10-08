"""A plausible report cannot substitute for evidence of the run that produced it."""

import hashlib
import json
import unittest

from scripts.evidence_envelope import EvidenceError, validate_envelope

REPORT = b'{"tests": {}}'


def envelope(**changes):
    return {
        "schema": 1,
        "access": "maintainer-admin",
        "run_id": "1" * 32,
        "kind": "acceptance",
        "runner_commit": "a" * 40,
        "catalogue_sha256": "b" * 64,
        "stories_sha256": "c" * 64,
        "expectations_sha256": "d" * 64,
        "selection_sha256": "e" * 64,
        "started_at": "2026-10-08T10:00:00Z",
        "finished_at": "2026-10-08T10:01:00Z",
        "state": "completed",
        "exit_code": 0,
        "report_sha256": hashlib.sha256(REPORT).hexdigest(),
        "server_commit": None,
    } | changes


class EvidenceEnvelopeTest(unittest.TestCase):
    def test_valid_envelope_preserves_unknown_server_and_does_not_claim_a_pass(self):
        record = envelope()
        result = validate_envelope(json.dumps(record).encode(), REPORT)
        self.assertEqual(result, record)
        self.assertIsNone(result["server_commit"])
        self.assertNotIn("passed", result)
        self.assertNotIn("trusted", result)

    def test_supplied_report_must_be_the_exact_recorded_bytes(self):
        with self.assertRaises(EvidenceError):
            validate_envelope(json.dumps(envelope()).encode(), REPORT + b" ")

    def test_interrupted_execution_remains_interrupted_even_with_a_report(self):
        record = envelope(state="interrupted", exit_code=None)
        result = validate_envelope(json.dumps(record).encode(), REPORT)
        self.assertEqual(result["state"], "interrupted")
        self.assertIsNone(result["exit_code"])

    def test_missing_report_is_recorded_as_unavailable_without_fabricating_zero_results(self):
        record = envelope(state="unavailable", exit_code=None, report_sha256=None)
        result = validate_envelope(json.dumps(record).encode(), None)
        self.assertEqual(result["state"], "unavailable")
        self.assertIsNone(result["report_sha256"])
        self.assertNotIn("tests", result)

    def test_failed_or_cancelled_runs_can_preserve_their_partial_report(self):
        for state in ("failed", "cancelled"):
            with self.subTest(state=state):
                record = envelope(state=state, exit_code=1)
                result = validate_envelope(json.dumps(record).encode(), REPORT)
                self.assertEqual((result["state"], result["exit_code"]), (state, 1))

    def test_completed_run_requires_a_report_and_a_zero_exit_status(self):
        for changes, report in (
            ({"exit_code": 1}, REPORT),
            ({"exit_code": None}, REPORT),
            ({"report_sha256": None}, None),
        ):
            with self.subTest(changes=changes), self.assertRaises(EvidenceError):
                validate_envelope(json.dumps(envelope(**changes)).encode(), report)

    def test_failed_unavailable_and_report_presence_cannot_contradict_each_other(self):
        cases = (
            (envelope(state="failed", exit_code=0), REPORT),
            (envelope(state="unavailable"), REPORT),
            (envelope(report_sha256=None), REPORT),
            (envelope(), None),
        )
        for record, report in cases:
            with self.subTest(record=record), self.assertRaises(EvidenceError):
                validate_envelope(json.dumps(record).encode(), report)

    def test_unknown_fields_cannot_smuggle_public_or_trusted_status(self):
        for field in ("public", "trusted", "workflow", "token", "profile_url"):
            with self.subTest(field=field), self.assertRaises(EvidenceError) as failure:
                validate_envelope(json.dumps(envelope(**{field: "SECRET-CANARY"})).encode(), REPORT)
            self.assertNotIn("SECRET-CANARY", str(failure.exception))

    def test_enum_and_numeric_types_are_closed(self):
        invalid = (
            ("schema", True),
            ("schema", 2),
            ("access", "public"),
            ("kind", "shell"),
            ("state", "passed"),
            ("exit_code", False),
            ("exit_code", 0.0),
            ("exit_code", -1),
            ("exit_code", 256),
        )
        for field, value in invalid:
            with self.subTest(field=field, value=value), self.assertRaises(EvidenceError):
                validate_envelope(json.dumps(envelope(**{field: value})).encode(), REPORT)

    def test_identifiers_are_exact_digests_not_paths_or_arbitrary_labels(self):
        for field in (
            "run_id",
            "runner_commit",
            "catalogue_sha256",
            "stories_sha256",
            "expectations_sha256",
            "selection_sha256",
            "server_commit",
        ):
            with self.subTest(field=field), self.assertRaises(EvidenceError):
                validate_envelope(json.dumps(envelope(**{field: "SECRET-CANARY"})).encode(), REPORT)

    def test_a_distinct_server_commit_does_not_get_replaced_by_runner_identity(self):
        record = envelope(kind="benchmark", server_commit="f" * 40)
        result = validate_envelope(json.dumps(record).encode(), REPORT)
        self.assertEqual(result["server_commit"], "f" * 40)
        self.assertNotEqual(result["server_commit"], result["runner_commit"])

    def test_time_requires_an_offset_and_nonnegative_duration(self):
        for finished in ("2026-10-08T10:01:00", "2026-10-08", "bad", "2026-10-08T09:59:59Z"):
            with self.subTest(finished=finished), self.assertRaises(EvidenceError):
                validate_envelope(json.dumps(envelope(finished_at=finished)).encode(), REPORT)

    def test_missing_fields_and_non_objects_are_rejected(self):
        record = envelope()
        del record["selection_sha256"]
        for value in (record, [], None):
            with self.subTest(value=value), self.assertRaises(EvidenceError):
                validate_envelope(json.dumps(value).encode(), REPORT)

    def test_duplicate_fields_and_nonfinite_json_are_not_silently_normalized(self):
        encoded = json.dumps(envelope())
        for raw in (
            encoded[:-1] + ', "access": "public"}',
            encoded.replace('"exit_code": 0', '"exit_code": NaN'),
            '{"broken":',
        ):
            with self.subTest(raw=raw), self.assertRaises(EvidenceError):
                validate_envelope(raw.encode(), REPORT)

    def test_size_limits_apply_before_parsing_or_hashing_unbounded_input(self):
        for raw, report in (
            (b" " * 65537, REPORT),
            (json.dumps(envelope()).encode(), b"x" * 16777217),
        ):
            with self.subTest(size=len(raw)), self.assertRaises(EvidenceError):
                validate_envelope(raw, report)

    def test_valid_timezone_offsets_are_compared_as_instants(self):
        record = envelope(finished_at="2026-10-08T12:01:00+02:00")
        self.assertEqual(validate_envelope(json.dumps(record).encode(), REPORT), record)

    def test_impossible_dates_invalid_encoding_and_excessive_nesting_are_rejected(self):
        for raw in (
            json.dumps(envelope(started_at="2026-02-30T10:00:00Z")).encode(),
            b"\xff",
            b"[" * 2000 + b"]" * 2000,
        ):
            with self.subTest(size=len(raw)), self.assertRaises(EvidenceError):
                validate_envelope(raw, REPORT)

    def test_failed_execution_without_a_report_is_not_lost(self):
        record = envelope(state="failed", exit_code=2, report_sha256=None)
        self.assertEqual(validate_envelope(json.dumps(record).encode(), None), record)
