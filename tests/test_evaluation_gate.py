"""The production activation gate uses candidate-specific, persisted evaluation evidence."""

import json
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from fakes import concept
from nci_si_mcp.embeddings import EmbeddingProvider, HashingEmbeddingProvider
from nci_si_mcp.errors import IndexBuildError, correlated
from nci_si_mcp.evaluation import evaluate_build
from nci_si_mcp.evaluation_sets import GoldQuery, MetricFloor, parse_set
from nci_si_mcp.index import LocalIndex
from test_evaluation_sets import sample_set_payload


class OrthogonalProvider(EmbeddingProvider):
    """Make semantic and lexical relevance disagree without mocking the ranker."""

    name = "test-only"
    model = "orthogonal"

    def embed(self, texts):
        vectors = {"query filler": [1.0, 0.0], "target": [0.0, 1.0], "query": [0.0, 1.0]}
        return [vectors[text] for text in texts]


class EvaluationGateTests(unittest.TestCase):
    def test_hit_at_five_independently_blocks_activation_when_mrr_passes(self):
        candidate = self.index.build(
            [concept(f"C{i}", "Identical") for i in range(1, 7)],
            None,
            self.provider,
            build_kind="production",
        )
        dataset = replace(
            self.dataset,
            queries=(GoldQuery("Identical", ("C6",)),),
            semantic=MetricFloor(hit_at_5=1, mrr_at_10=0.1),
            hybrid=MetricFloor(hit_at_5=1, mrr_at_10=0.1),
        )
        evaluated = evaluate_build(self.index, self.provider, dataset, candidate.build_id)
        report = evaluated.evaluation_report
        self.assertEqual(report["gold_codes_not_indexed"], [])
        for result in report["results"]:
            with self.subTest(mode=result["mode"]):
                self.assertEqual(result["hit_at_5"], 0)
                self.assertAlmostEqual(result["mean_reciprocal_rank"], 1 / 6)
        self.assertFalse(report["passed"])
        with self.assertRaisesRegex(IndexBuildError, "passing evaluation"):
            self.index.activate(candidate.build_id)
        self.assertIsNone(self.index.get_active_manifest())

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.index = LocalIndex(self.directory.name)
        self.provider = HashingEmbeddingProvider()
        self.rows = [concept("C3262", "Neoplasm"), concept("C8", "melanoma")]
        self.dataset = replace(parse_set(sample_set_payload()), release=self.rows[0]["version"])

    def build(self):
        return self.index.build(self.rows, None, self.provider, build_kind="production")

    def test_missing_evaluation_blocks_activation_without_changing_active_snapshot(self):
        self.index.upsert_concepts([self.rows[1]], None, self.provider)
        active = self.index.get_active_manifest()
        candidate = self.build()
        with self.assertRaisesRegex(IndexBuildError, "passing evaluation"):
            self.index.activate(candidate.build_id)
        self.assertEqual(self.index.get_active_manifest(), active)
        self.assertEqual(self.index.get_concept("C8").preferred_name, "melanoma")
        self.assertIsNone(self.index.get_concept("C3262"))

    def test_passing_report_survives_reopen_and_enables_activation_and_rollback(self):
        self.index.upsert_concepts([self.rows[1]], None, self.provider)
        previous = self.index.get_active_manifest()
        candidate = self.build()
        evaluated = evaluate_build(self.index, self.provider, self.dataset, candidate.build_id)
        self.assertFalse(evaluated.active)
        self.assertTrue(evaluated.evaluation_report["passed"])
        self.assertTrue(evaluated.evaluation_report["test_only"])
        self.assertEqual(evaluated.evaluation_score, 1)
        self.assertEqual(evaluated.evaluation_version, self.dataset.version)
        reopened = LocalIndex(self.directory.name)
        active = reopened.activate(candidate.build_id)
        self.assertEqual(active.evaluation_report, evaluated.evaluation_report)
        self.assertEqual(active.build_kind, "production")
        self.assertEqual(reopened.activate(previous.build_id).build_id, previous.build_id)
        with correlated():
            self.assertNotIn("evaluation_report", active.to_result())
            self.assertNotIn("build_kind", active.to_result())

    def test_failed_metric_report_is_preserved_and_blocks_activation(self):
        candidate = self.build()
        dataset = replace(self.dataset, queries=(GoldQuery("Neoplasm", ("C8",)),))
        evaluated = evaluate_build(self.index, self.provider, dataset, candidate.build_id)
        self.assertFalse(evaluated.evaluation_report["passed"])
        self.assertEqual(evaluated.evaluation_report["gold_codes_not_indexed"], [])
        self.assertLess(evaluated.evaluation_score, dataset.semantic.mrr_at_10)
        with self.assertRaisesRegex(IndexBuildError, "passing evaluation"):
            self.index.activate(candidate.build_id)
        self.assertIsNone(self.index.get_active_manifest())

    def test_semantic_and_hybrid_failures_each_independently_block_activation(self):
        provider = OrthogonalProvider()
        candidate = self.index.build(
            [concept("C3262", "query filler"), concept("C8", "target")],
            None,
            provider,
            build_kind="production",
        )
        for failed_mode, expected_code in (("vector", "C3262"), ("hybrid", "C8")):
            with self.subTest(failed_mode=failed_mode):
                dataset = replace(
                    self.dataset,
                    embedding_provider=provider.name,
                    embedding_model=provider.model,
                    embedding_dimensions=2,
                    queries=(GoldQuery("query", (expected_code,)),),
                )
                evaluated = evaluate_build(self.index, provider, dataset, candidate.build_id)
                results = {row["mode"]: row for row in evaluated.evaluation_report["results"]}
                other = "hybrid" if failed_mode == "vector" else "vector"
                self.assertEqual(results[failed_mode]["mean_reciprocal_rank"], 0.5)
                self.assertEqual(results[other]["mean_reciprocal_rank"], 1)
                self.assertFalse(evaluated.evaluation_report["passed"])
                with self.assertRaises(IndexBuildError):
                    self.index.activate(candidate.build_id)

    def test_one_missing_alternative_fails_even_when_another_expected_code_ranks_first(self):
        candidate = self.build()
        dataset = replace(self.dataset, queries=(GoldQuery("Neoplasm", ("C3262", "C999")),))
        evaluated = evaluate_build(self.index, self.provider, dataset, candidate.build_id)
        report = evaluated.evaluation_report
        self.assertFalse(report["passed"])
        self.assertEqual(report["gold_codes_not_indexed"], ["C999"])
        self.assertEqual(report["results"][0]["query_count"], 1)
        self.assertEqual(report["results"][0]["hit_at_1"], 1)
        with self.assertRaises(IndexBuildError):
            self.index.activate(candidate.build_id)

    def test_rebuild_inherits_production_classification_but_not_prior_evaluation(self):
        candidate = self.build()
        evaluate_build(self.index, self.provider, self.dataset, candidate.build_id)
        self.index.activate(candidate.build_id)
        rebuilt = self.index.rebuild(candidate.build_id, self.provider)
        self.assertEqual(rebuilt.build_kind, "production")
        self.assertIsNone(rebuilt.evaluation_report)
        self.assertIsNone(rebuilt.evaluation_version)
        with self.assertRaises(IndexBuildError):
            self.index.activate(rebuilt.build_id)
        self.assertEqual(self.index.get_active_manifest().build_id, candidate.build_id)

    def test_sample_update_cannot_remove_production_evaluation_requirement(self):
        candidate = self.build()
        evaluate_build(self.index, self.provider, self.dataset, candidate.build_id)
        active = self.index.activate(candidate.build_id)
        with self.assertRaisesRegex(IndexBuildError, "separate data directory"):
            self.index.upsert_concepts([concept("C999", "new sample")], None, self.provider)
        self.assertEqual(self.index.get_active_manifest(), active)
        self.assertIsNone(self.index.get_concept("C999"))

    def test_report_for_another_candidate_cannot_authorize_activation(self):
        first = self.build()
        evaluated = evaluate_build(self.index, self.provider, self.dataset, first.build_id)
        second = self.build()
        with self.assertRaisesRegex(IndexBuildError, "identity"):
            self.index.record_evaluation(second.build_id, evaluated.evaluation_report)
        with self.assertRaises(IndexBuildError):
            self.index.activate(second.build_id)

    def test_calibration_identity_mismatch_does_not_store_an_evaluation(self):
        candidate = self.build()
        for change in (
            {"release": "other"},
            {"embedding_model": "other"},
            {"embedding_provider": "other"},
            {"embedding_dimensions": 768},
        ):
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(IndexBuildError, "calibration"),
            ):
                evaluate_build(
                    self.index, self.provider, replace(self.dataset, **change), candidate.build_id
                )
        self.assertIsNone(self.index.list_builds()[0].evaluation_report)

    def test_sample_rebuild_stays_a_sample_and_needs_no_production_report(self):
        sample = self.index.build(self.rows, None, self.provider)
        rebuilt = self.index.rebuild(sample.build_id, self.provider)
        self.assertEqual(rebuilt.build_kind, "sample")
        self.assertTrue(self.index.activate(rebuilt.build_id).active)
        self.assertIsNone(self.index.get_active_manifest().evaluation_report)

    def test_candidate_removed_before_report_write_cannot_be_resurrected_as_evaluated(self):
        candidate = self.build()
        other = self.index.build([self.rows[1]], None, self.provider)
        record = self.index.record_evaluation

        def activate_before_report(build_id, report):
            self.index.activate(other.build_id)
            return record(build_id, report)

        with (
            patch.object(self.index, "record_evaluation", side_effect=activate_before_report),
            self.assertRaisesRegex(IndexBuildError, "unavailable"),
        ):
            evaluate_build(self.index, self.provider, self.dataset, candidate.build_id)
        self.assertEqual([build.build_id for build in self.index.list_builds()], [other.build_id])
        self.assertIsNone(self.index.get_active_manifest().evaluation_report)

    def test_unclassified_rebuild_requires_evaluation_and_explains_both_migration_paths(self):
        original = self.index.build(self.rows, None, self.provider)
        payload = original.to_dict()
        del payload["build_kind"]
        with self.index._connect() as conn:
            conn.execute("UPDATE manifests SET payload = ?", (json.dumps(payload),))
        legacy = self.index.activate(original.build_id)
        with self.assertRaisesRegex(IndexBuildError, "separate data directory"):
            self.index.upsert_concepts(self.rows, None, self.provider)
        self.assertEqual(self.index.get_active_manifest(), legacy)
        rebuilt = self.index.rebuild(original.build_id, self.provider)
        self.assertEqual(rebuilt.build_kind, "production")
        self.assertEqual(rebuilt.unclassified_source_build, original.build_id)
        with self.assertRaises(IndexBuildError) as raised:
            self.index.activate(rebuilt.build_id)
        message = str(raised.exception)
        self.assertIn(f"Snapshot {original.build_id} is unclassified", message)
        self.assertIn(f"Evaluate rebuild {rebuilt.build_id}", message)
        self.assertIn("recreate it with index-sample in a separate data directory", message)
        self.assertEqual(self.index.activate(original.build_id), legacy)
        self.assertIsNone(legacy.evaluation_report)
        evaluate_build(self.index, self.provider, self.dataset, rebuilt.build_id)
        self.assertTrue(self.index.activate(rebuilt.build_id).active)
        self.assertEqual(self.index.activate(original.build_id), legacy)

    def test_unrecognized_classification_cannot_bypass_the_gate(self):
        candidate = self.index.build(self.rows, None, self.provider)
        payload = candidate.to_dict() | {"build_kind": "unknown"}
        with self.index._connect() as conn:
            conn.execute("UPDATE manifests SET payload = ?", (json.dumps(payload),))
        with self.assertRaisesRegex(IndexBuildError, "unknown classification"):
            self.index.activate(candidate.build_id)
        with self.assertRaisesRegex(IndexBuildError, "unknown classification"):
            evaluate_build(self.index, self.provider, self.dataset, candidate.build_id)
        self.assertIsNone(self.index.get_active_manifest())
