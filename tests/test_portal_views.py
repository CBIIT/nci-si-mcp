"""Result pages explain provenance and incompleteness without JavaScript or raw evidence."""

import unittest

from scripts.evidence_http import PHASE_LABELS
from scripts.portal_views import comparison_page, history_page, run_page

from test_evidence_acceptance import project
from test_evidence_benchmark import project as benchmark_projection
from test_evidence_benchmark import selected_projection


class PortalViewsTest(unittest.TestCase):
    def record(self, evidence):
        return {
            "sequence": 1,
            "checksum": "a" * 64,
            "origin": "local-import-unverified",
            "evidence": evidence,
        }

    def test_empty_history_says_no_attempt_instead_of_zero_passes(self):
        html = history_page([])
        self.assertIn("No recorded attempt", html)
        self.assertIn("No complete evidence", html)
        self.assertNotIn("View run", html)
        self.assertIn("Import your first report", html)

    def test_history_distinguishes_latest_interruption_from_earlier_complete_evidence(self):
        rows = [
            {
                "run_id": "2" * 32,
                "sequence": 2,
                "kind": "acceptance",
                "state": "interrupted",
                "inventory_complete": False,
                "mode": None,
            },
            {
                "run_id": "1" * 32,
                "sequence": 1,
                "kind": "acceptance",
                "state": "completed",
                "inventory_complete": True,
                "mode": "fixture",
            },
        ]
        html = history_page(rows)
        self.assertIn("Latest attempt", html)
        self.assertIn("Latest complete evidence", html)
        self.assertIn("interrupted", html)
        self.assertIn("fixture", html)
        self.assertIn("not a PASS verdict", html)
        self.assertIn("View run 2", html)
        self.assertIn("Interrupted", html)

    def test_tool_and_story_filters_keep_native_verdict_and_show_count(self):
        record = {
            "sequence": 1,
            "checksum": "a" * 64,
            "origin": "local-import-unverified",
            "evidence": project(),
        }
        html = run_page(record, tool="lookup", story="find-a-concept")
        self.assertIn("1 of 2 cases", html)
        self.assertIn("PASS", html)
        self.assertIn("Origin unverified", html)
        self.assertIn("Server identity not independently verified", html)
        self.assertIn("Inventory complete", html)
        self.assertIn("Runner source commit", html)
        self.assertNotIn("inventory_complete", html)
        self.assertIn('<form method="get"', html)
        self.assertNotIn("test_example.py", html)

    def test_unmatched_filter_reports_no_rows_without_changing_evidence(self):
        record = {
            "sequence": 1,
            "checksum": "a" * 64,
            "origin": "local-import-unverified",
            "evidence": project(),
        }
        html = run_page(record, tool='"><script>CANARY</script>')
        self.assertIn("0 of 2 cases", html)
        self.assertNotIn("<script>CANARY", html)
        self.assertIn("&lt;script&gt;CANARY", html)

    def test_unverified_legacy_report_has_no_results_or_pass_claim(self):
        record = {
            "sequence": 1,
            "checksum": "a" * 64,
            "origin": "local-import-unverified",
            "evidence": {
                "run_id": "1" * 32,
                "kind": "acceptance",
                "state": "unverified",
                "inventory_complete": False,
            },
        }
        html = run_page(record)
        self.assertIn("Original inventory unavailable", html)
        self.assertNotIn("PASS", html)

    def test_benchmark_latency_always_has_error_and_sample_counts(self):
        html = run_page(self.record(benchmark_projection()))
        self.assertIn("Samples", html)
        self.assertIn("Errors", html)
        self.assertIn("<td>2</td><td>1</td><td>10.0</td><td>30.0</td>", html)
        self.assertIn("Low sample counts", html)
        self.assertIn("unknown blocks comparison", html)

    def test_wide_result_tables_have_named_keyboard_scroll_regions(self):
        html = run_page(self.record(benchmark_projection()))
        self.assertIn('tabindex="0" role="region" aria-label="Benchmark measurements"', html)
        self.assertIn("<caption>Benchmark measurements</caption>", html)
        self.assertIn('<th scope="col">Samples</th>', html)

    def test_http_results_label_client_sessions_and_show_excluded_warmup_errors(self):
        evidence = benchmark_projection()
        evidence["phase_labels"] = PHASE_LABELS
        evidence["cases"][0]["warmups"] = evidence["cases"][0]["warm"]
        html = run_page(self.record(evidence))
        self.assertIn("First call in new client session", html)
        self.assertIn("Warmed client session", html)
        self.assertIn("Warm-up (excluded from measured phases)", html)
        self.assertNotIn("<td>cold</td>", html)

    def test_unknown_fingerprints_block_comparison(self):
        record = self.record(benchmark_projection())
        html = comparison_page(record, record)
        self.assertIn("Comparison blocked", html)
        self.assertIn("unverified-selection", html)
        self.assertNotIn("Recorded fingerprints match", html)

    def test_matching_fingerprints_allow_descriptive_comparison_without_claiming_origin(self):
        record = self.record(selected_projection())
        html = comparison_page(record, record)
        self.assertIn("Recorded fingerprints match", html)
        self.assertIn("origin and server identity remain unverified", html)
        self.assertIn("Samples", html)

    def test_acceptance_records_cannot_be_compared_as_benchmarks(self):
        record = self.record(project())
        self.assertIn("Comparison unavailable", comparison_page(record, record))

    def test_history_offers_comparison_controls_without_javascript(self):
        html = history_page(
            [
                {
                    "run_id": "1" * 32,
                    "sequence": 3,
                    "kind": "benchmark",
                    "state": "completed",
                    "mode": "fixture",
                    "inventory_complete": True,
                }
            ]
        )
        self.assertIn('<form action="/compare" method="get">', html)
        self.assertIn('name="left"', html)
        self.assertIn('name="right"', html)
        self.assertIn('value="' + "1" * 32 + '"', html)
