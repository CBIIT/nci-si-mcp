import unittest

from nci_si_mcp.task_evaluation import assess, summarize, validate_plan


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
    }


class TaskEvaluationTest(unittest.TestCase):
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
        row = assess(case, run() | {"results": [{"error": {"code": "capability_unavailable"}}]})
        self.assertTrue(row["correct"])
        self.assertFalse(row["content_obtained"])

    def test_invalid_or_over_budget_metrics_cannot_create_a_success(self):
        for changes in (
            {"requests": 4},
            {"requests": -1},
            {"requests": 0.5},
            {"elapsed_ms": float("nan")},
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
