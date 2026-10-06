"""Ranking regressions exercise observable pages, scores and winning fields."""

from unittest.mock import patch

from fakes import concept
from nci_si_mcp.embeddings import HashingEmbeddingProvider
from nci_si_mcp.errors import IndexStorageError, PlatformError
from test_index import IndexTestCase


class ScoringReviewTest(IndexTestCase):
    def test_exact_name_beats_a_better_vector_before_page_selection(self):
        provider = HashingEmbeddingProvider(dimensions=2)
        values = {"Exact": [1.0, 0.0], "Other": [0.0, 1.0]}
        with patch.object(provider, "embed", side_effect=lambda texts: [values[t] for t in texts]):
            index = self.build([concept("C9", "Exact"), concept("C1", "Other")], provider=provider)
        with patch.object(provider, "embed", return_value=[[0.0, 1.0]]):
            hits = index.search("Exact", provider, mode="vector", limit=1)
        self.assertEqual(
            [(hit.concept.code, hit.score, hit.matched_on) for hit in hits], [("C9", 1.0, "name")]
        )

    def test_bm25_exact_casefold_match_is_injected_and_wins_score_ties(self):
        index = self.build(
            [
                concept("C1", "Other", synonyms=[{"name": "STRASSE"}]),
                concept("C9", "Straße"),
            ]
        )
        hits = index.search("STRASSE", self.provider, mode="bm25", limit=1)
        self.assertEqual(
            [(hit.concept.code, hit.score, hit.matched_on) for hit in hits], [("C9", 1.0, "name")]
        )

    def test_bm25_ties_use_code_order_and_continue_after_the_first_page(self):
        index = self.build(
            [concept(code, "Other", synonyms=[{"name": "needle"}]) for code in ("C9", "C1", "C5")]
        )
        # A valid query plan can visit equal matches in insertion order.
        # The public ordering must not depend on the optional lookup index.
        with index._connect() as conn:
            conn.execute("DROP INDEX fields_concept")
        first, total, _ = index.search_page("needle", self.provider, mode="bm25", limit=1)
        second, _, _ = index.search_page("needle", self.provider, mode="bm25", limit=1, offset=1)
        self.assertEqual(total, 3)
        self.assertEqual([first[0].concept.code, second[0].concept.code], ["C1", "C5"])
        self.assertEqual([first[0].score, second[0].score], [1.0, 1.0])

    def test_bm25_equal_fields_prefer_name_over_synonym_and_definition(self):
        index = self.build(
            [
                concept(
                    "C1",
                    "alpha beta",
                    synonyms=[{"name": "beta alpha"}],
                    definitions=[{"definition": "alpha  beta"}],
                )
            ]
        )
        hits = index.search("alpha", self.provider, mode="bm25")
        self.assertEqual(
            [(hit.concept.code, hit.matched_on, hit.score) for hit in hits], [("C1", "name", 1.0)]
        )

    def test_hybrid_lexical_scores_stay_with_their_concepts_after_a_scan_chunk(self):
        rows = [concept(f"C{i:03}", "noise") for i in range(256)] + [concept("C999", "needle")]
        with patch.object(
            self.provider, "embed", side_effect=lambda texts: [[1.0, 0.0] for _ in texts]
        ):
            index = self.build(rows)
            hits = index.search("needle query", self.provider, mode="hybrid", limit=2)
        self.assertEqual(hits[0].concept.code, "C999")
        self.assertEqual(hits[0].score_components["bm25"], 1.0)
        self.assertEqual(hits[1].score_components["bm25"], 0.0)

    def test_vector_mode_never_reports_a_bm25_component_even_for_exact_names(self):
        index = self.build([concept("C1", "Alpha"), concept("C2", "Alpha Beta")])
        hits = index.search("Alpha", self.provider, mode="vector")
        self.assertEqual([hit.concept.code for hit in hits], ["C1", "C2"])
        self.assertEqual([hit.score_components["bm25"] for hit in hits], [0.0, 0.0])

    def test_vector_nonexact_matches_also_have_no_bm25_component(self):
        index = self.build([concept("C1", "Alpha Beta"), concept("C2", "Alpha Gamma")])
        hits = index.search("Alpha", self.provider, mode="vector")
        self.assertEqual({hit.concept.code for hit in hits}, {"C1", "C2"})
        self.assertEqual([hit.score_components["bm25"] for hit in hits], [0.0, 0.0])

    def test_positive_cosines_normalize_the_weakest_match_to_zero(self):
        provider = HashingEmbeddingProvider(dimensions=2)
        values = {"Alpha": [1.0, 0.0], "Beta": [1.0, 1.0], "query": [1.0, 0.0]}
        with patch.object(provider, "embed", side_effect=lambda texts: [values[t] for t in texts]):
            index = self.build([concept("C1", "Alpha"), concept("C2", "Beta")], provider=provider)
            hits = index.search("query", provider, mode="vector")
        self.assertEqual(
            [(hit.concept.code, hit.score) for hit in hits], [("C1", 1.0), ("C2", 0.0)]
        )

    def test_no_matching_retirement_status_has_zero_total_and_no_hits(self):
        index = self.build([concept("C1", "Alpha", active=True)])
        hits, total, manifest = index.search_page(
            "Alpha", self.provider, retired_status="Retired_Concept"
        )
        self.assertEqual((hits, total), ([], 0))
        self.assertEqual(manifest.concept_count, 1)

    def test_nonbinary_vector_kind_storage_is_reported_as_database_corruption(self):
        index = self.build([concept("C1", "Alpha")])
        for kinds in ("x", 1):
            with self.subTest(kinds=kinds):
                with index._connect() as conn:
                    conn.execute("UPDATE concept_vectors SET kinds = ?", (kinds,))
                with self.assertRaisesRegex(IndexStorageError, "Stored vector length"):
                    index.search("Alpha", self.provider, mode="vector")

    def test_missing_numpy_names_the_unavailable_scoring_capability(self):
        index = self.build([concept("C1", "Alpha")])
        with patch.dict("sys.modules", {"numpy": None}), self.assertRaises(PlatformError) as raised:
            index.search("Alpha", self.provider, mode="vector")
        self.assertEqual(raised.exception.code, "capability_unavailable")
        self.assertEqual(raised.exception.details, {"capability": "semantic/hybrid index scoring"})
