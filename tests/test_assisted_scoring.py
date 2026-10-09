import unittest

from scripts.assisted_scoring import assess, summarize, validate_plan


def task():
    return {
        "id": "concept",
        "expect": {"0/code": "C1", "0/provenance/release/identifier": "26.09d"},
        "outcome": "content",
        "max_requests": 3,
        "calls": [{"name": "get_concept", "arguments": {"code": "C1"}}],
    }


def run():
    return {
        "status": "completed",
        "results": [{"code": "C1", "provenance": {"release": {"identifier": "26.09d"}}}],
        "elapsed_ms": 10.0,
        "requests": 1,
        "bytes": 80,
        "unmatched_requests": 0,
        "protocol_errors": [False],
    }


class AssistedScoringTest(unittest.TestCase):
    def test_execution_plan_changes_cannot_rewrite_preregistered_recipe(self):
        case = task()
        execution = validate_plan(case, case["calls"])
        execution[0]["arguments"]["code"] = "C2"
        execution.append({"name": "get_concept", "arguments": {"code": "C3"}})
        self.assertEqual(case["calls"], [{"name": "get_concept", "arguments": {"code": "C1"}}])

    def test_fixture_provenance_requires_an_explicit_integer_zero(self):
        for measurement in (None, False, 0.0, -1, 1):
            with self.subTest(measurement=measurement):
                record = run() | {"unmatched_requests": measurement}
                self.assertFalse(assess(task(), record)["correct"])
        record = run()
        del record["unmatched_requests"]
        self.assertFalse(assess(task(), record)["correct"])

    def test_protocol_failure_cannot_be_scored_as_content(self):
        row = assess(task(), run() | {"protocol_errors": [True]})
        self.assertFalse(row["correct"])
        self.assertFalse(row["content_obtained"])

    def test_error_record_without_protocol_failure_cannot_score_as_refusal(self):
        case = task() | {"expect": {"0/error/code": "permission_denied"}, "outcome": "refusal"}
        record = run() | {"results": [{"error": {"code": "permission_denied"}}]}
        self.assertFalse(assess(case, record)["correct"])

    def test_missing_malformed_or_misaligned_protocol_flags_cannot_pass(self):
        for flags in (None, False, [], [0], [None], ["false"], [False, False], {0: False}):
            with self.subTest(flags=flags):
                self.assertFalse(assess(task(), run() | {"protocol_errors": flags})["correct"])
        record = run()
        del record["protocol_errors"]
        self.assertFalse(assess(task(), record)["correct"])

    def test_protocol_evidence_checks_each_response_and_rejects_nonobjects(self):
        record = run()
        record["results"].append({"error": {"code": "permission_denied"}})
        record["protocol_errors"] = [False, True]
        case = task() | {"calls": task()["calls"] * 2, "outcome": "refusal"}
        self.assertTrue(assess(case, record)["checks"]["protocol_consistent"])
        record["protocol_errors"] = [True, True]
        self.assertFalse(assess(case, record)["checks"]["protocol_consistent"])
        record["results"] = [None]
        record["protocol_errors"] = [False]
        self.assertFalse(assess(task(), record)["checks"]["protocol_consistent"])

    def test_an_error_or_partial_record_is_never_scored_as_complete_content(self):
        for extra in (
            {"error": {"code": "upstream_unavailable"}},
            {"truncation": {"occurred": True}},
        ):
            with self.subTest(extra=extra):
                record = run()
                record["results"][0].update(extra)
                self.assertFalse(assess(task(), record)["content_obtained"])

    def test_forged_extra_evidence_or_wrong_field_types_cannot_satisfy_a_task(self):
        record = run()
        record["results"].append({"error": {"code": "upstream_unavailable"}})
        self.assertFalse(assess(task(), record)["correct"])
        case = task() | {"expect": {"0/active": True}}
        self.assertFalse(assess(case, run() | {"results": [{"active": 1}]})["correct"])

    def test_response_count_must_match_recipe_even_when_each_response_is_valid(self):
        record = run()
        record["results"] *= 2
        record["protocol_errors"] *= 2
        self.assertFalse(assess(task(), record)["correct"])
        case = task() | {"calls": task()["calls"] * 2}
        self.assertFalse(assess(case, run())["correct"])

    def test_a_task_without_expected_evidence_cannot_claim_success(self):
        row = assess(task() | {"expect": {}}, run())
        self.assertFalse(row["correct"])
        self.assertFalse(row["content_obtained"])

    def test_plan_whitelist_and_argument_types_are_enforced(self):
        case = task() | {"calls": [{"name": "ask", "arguments": {}}]}
        with self.assertRaises(ValueError):
            validate_plan(case, case["calls"])
        case = task() | {"calls": [{"name": "get_concept", "arguments": {"code": 1}}]}
        with self.assertRaises(ValueError):
            validate_plan(case, [{"name": "get_concept", "arguments": {"code": True}}])

    def test_correct_identifier_and_release_are_required(self):
        record = run()
        self.assertTrue(assess(task(), record)["correct"])
        record["results"][0]["provenance"]["release"]["identifier"] = "other"
        self.assertFalse(assess(task(), record)["correct"])
        record["results"] = []
        self.assertFalse(assess(task(), record)["correct"])

    def test_cancelled_and_incomplete_runs_stay_in_the_denominator(self):
        rows = [
            assess(task(), run()),
            assess(task(), run() | {"status": "cancelled"}),
            assess(task(), {"status": "incomplete"}),
        ]
        summary = summarize(rows)
        self.assertEqual(summary["runs"], 3)
        self.assertEqual(summary["correct"], 1)
        self.assertAlmostEqual(summary["correct_rate"], 1 / 3)

    def test_correct_refusal_does_not_count_as_content_obtained(self):
        case = task() | {"expect": {"0/error/code": "capability_unavailable"}, "outcome": "refusal"}
        row = assess(
            case,
            run()
            | {
                "results": [{"error": {"code": "capability_unavailable"}}],
                "protocol_errors": [True],
            },
        )
        self.assertTrue(row["correct"])
        self.assertFalse(row["content_obtained"])

    def test_invalid_or_over_budget_metrics_cannot_create_a_success(self):
        for changes in (
            {"requests": 4},
            {"requests": -1},
            {"requests": 0.5},
            {"elapsed_ms": float("nan")},
            {"elapsed_ms": float("inf")},
            {"bytes": -1},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(assess(task(), run() | changes)["correct"])

    def test_summary_retains_failed_latency_and_explicit_empty_state(self):
        rows = [
            assess(task(), run()),
            assess(task(), run() | {"elapsed_ms": 90, "status": "failed"}),
        ]
        summary = summarize(rows)
        self.assertEqual(summary["latency_ms"], {"samples": 2, "p50": 50.0, "p95": 90})
        self.assertEqual(summarize([])["correct_rate"], None)

    def test_invented_recursive_or_changed_plans_are_rejected_before_execution(self):
        approved = task()["calls"]
        self.assertEqual(validate_plan(task(), approved), approved)
        for proposed in (
            [{"name": "ask", "arguments": {}}],
            approved * 2,
            [{"name": "get_concept", "arguments": {"url": "https://unapproved.invalid"}}],
        ):
            with self.subTest(proposed=proposed), self.assertRaises(ValueError):
                validate_plan(task(), proposed)
