import tempfile
import unittest

from fakes import concept
from nci_si_mcp.embeddings import HashingEmbeddingProvider
from nci_si_mcp.evaluation import GoldQuery, evaluate_retrieval
from nci_si_mcp.index import LocalIndex

# "sarcoma" matches seven concepts. BM25 ranks them by how often the term
# occurs, so C1 is first, C2 second and C7 seventh.
CONCEPTS = [
    concept(f"C{rank}", " ".join(["sarcoma"] * (8 - rank) + [f"filler{rank}"] * rank))
    for rank in range(1, 8)
] + [concept("C8", "unrelated melanoma")]


class EvaluationTest(unittest.TestCase):
    def evaluate(self, expected_codes):
        with tempfile.TemporaryDirectory() as tmp_path:
            index = LocalIndex(tmp_path)
            provider = HashingEmbeddingProvider()
            index.upsert_concepts(CONCEPTS, "2026-06-29", provider)
            return evaluate_retrieval(
                index,
                provider,
                gold_queries=[GoldQuery("sarcoma", codes) for codes in expected_codes],
                modes=["bm25"],
            )[0]

    def test_metrics_follow_the_rank_of_the_first_expected_code(self):
        cases = {
            "first": ([["C1"]], (1.0, 1.0, 1.0)),
            "second": ([["C2"]], (0.0, 1.0, 0.5)),
            "beyond five": ([["C7"]], (0.0, 0.0, 1 / 7)),
            "best of several": ([["C7", "C2"]], (0.0, 1.0, 0.5)),
            "missing": ([["C8"]], (0.0, 0.0, 0.0)),
            "averaged": ([["C1"], ["C2"], ["C8"], ["C7"]], (0.25, 0.5, (1 + 0.5 + 0 + 1 / 7) / 4)),
        }
        for label, (expected_codes, metrics) in cases.items():
            with self.subTest(label):
                result = self.evaluate(expected_codes)
                self.assertEqual(result.query_count, len(expected_codes))
                self.assertAlmostEqual(result.hit_at_1, metrics[0])
                self.assertAlmostEqual(result.hit_at_5, metrics[1])
                self.assertAlmostEqual(result.mean_reciprocal_rank, metrics[2])

    def test_every_mode_is_evaluated_by_default(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            index = LocalIndex(tmp_path)
            provider = HashingEmbeddingProvider()
            index.upsert_concepts(CONCEPTS, "2026-06-29", provider)

            results = evaluate_retrieval(
                index, provider, gold_queries=[GoldQuery("melanoma", ["C8"])]
            )

        self.assertEqual([result.mode for result in results], ["bm25", "vector", "hybrid"])
        for result in results:
            self.assertEqual(result.hit_at_1, 1.0)
            self.assertEqual(result.to_dict()["mode"], result.mode)


if __name__ == "__main__":
    unittest.main()
