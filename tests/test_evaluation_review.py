"""Candidate activation requires the calibrated metrics and matching evidence."""

import json
from dataclasses import replace

from fakes import concept
from nci_si_mcp.errors import IndexBuildError
from nci_si_mcp.evaluation import evaluate_build, evaluate_retrieval
from nci_si_mcp.evaluation_sets import GoldQuery, MetricFloor, parse_set, production_set
from nci_si_mcp.index import LocalIndex
from test_evaluation_gate import OrthogonalProvider
from test_evaluation_sets import sample_set_payload
from test_index import IndexTestCase


class EvaluationReviewTest(IndexTestCase):
    def candidate(self):
        index = LocalIndex(self.path)
        build = index.build(
            [concept("C3262", "Neoplasm")], None, self.provider, build_kind="production"
        )
        dataset = replace(parse_set(sample_set_payload()), release="26.06e")
        evaluated = evaluate_build(index, self.provider, dataset, build.build_id)
        self.assertTrue(evaluated.evaluation_report["passed"])
        return index, evaluated

    def test_rank_five_counts_in_hit_at_five(self):
        index = self.build([concept(f"C{i}", "Same") for i in range(1, 7)])
        results = evaluate_retrieval(index, self.provider, [GoldQuery("Same", ("C5",))])
        for result in results:
            with self.subTest(mode=result.mode):
                self.assertEqual(result.per_query[0].first_expected_rank, 5)
                self.assertEqual(result.hit_at_5, 1.0)
                self.assertEqual(result.mean_reciprocal_rank, 0.2)

    def test_distinct_mode_floors_and_report_use_the_weaker_mrr(self):
        provider = OrthogonalProvider()
        index = LocalIndex(self.path)
        build = index.build(
            [concept("C3262", "query filler"), concept("C8", "target")],
            None,
            provider,
            build_kind="production",
        )
        dataset = replace(
            parse_set(sample_set_payload()),
            release="26.06e",
            embedding_provider=provider.name,
            embedding_model=provider.model,
            embedding_dimensions=2,
            queries=(GoldQuery("query", ("C8",)),),
            semantic=MetricFloor(1, 1),
            hybrid=MetricFloor(1, 0.5),
        )
        evaluated = evaluate_build(index, provider, dataset, build.build_id)
        self.assertTrue(evaluated.evaluation_report["passed"])
        self.assertEqual(evaluated.evaluation_score, 0.5)
        self.assertEqual(
            evaluated.evaluation_report["thresholds"],
            {
                "vector": {"hit_at_5": 1, "mrr_at_10": 1},
                "hybrid": {"hit_at_5": 1, "mrr_at_10": 0.5},
            },
        )
        self.assertTrue(index.activate(build.build_id).active)

    def test_shipped_calibration_names_the_measured_release(self):
        self.assertEqual(production_set().release, "26.09d")

    def alter_manifest(self, index, manifest, **changes):
        with index._connect() as conn:
            conn.execute(
                "UPDATE manifests SET payload = ? WHERE build_id = ?",
                (json.dumps(manifest.to_dict() | changes), manifest.build_id),
            )

    def test_activation_refuses_a_report_whose_candidate_identity_changed(self):
        index, evaluated = self.candidate()
        self.alter_manifest(index, evaluated, concept_count=2)
        with self.assertRaisesRegex(IndexBuildError, "passing evaluation"):
            index.activate(evaluated.build_id)
        self.assertIsNone(index.get_active_manifest())

    def test_activation_requires_an_evaluation_version_even_when_both_are_null(self):
        index, evaluated = self.candidate()
        report = evaluated.evaluation_report | {"evaluation_version": None}
        self.alter_manifest(index, evaluated, evaluation_version=None, evaluation_report=report)
        with self.assertRaisesRegex(IndexBuildError, "passing evaluation"):
            index.activate(evaluated.build_id)
        self.assertIsNone(index.get_active_manifest())

    def test_activation_refuses_another_evaluation_version(self):
        index, evaluated = self.candidate()
        self.alter_manifest(index, evaluated, evaluation_version="other-calibration")
        with self.assertRaisesRegex(IndexBuildError, "passing evaluation"):
            index.activate(evaluated.build_id)
        self.assertIsNone(index.get_active_manifest())

    def test_recording_evidence_checks_both_embedding_provider_and_model(self):
        index, evaluated = self.candidate()
        for field in ("embedding_provider", "embedding_model"):
            with self.subTest(field=field), self.assertRaisesRegex(IndexBuildError, "identity"):
                index.record_evaluation(
                    evaluated.build_id, evaluated.evaluation_report | {field: "other"}
                )
        self.assertEqual(index.list_builds()[0].evaluation_report, evaluated.evaluation_report)
