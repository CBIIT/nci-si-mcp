import tempfile
import unittest

from nci_si_mcp.embeddings import HashingEmbeddingProvider
from nci_si_mcp.evaluation import GoldQuery, evaluate_retrieval
from nci_si_mcp.index import LocalIndex


class EvaluationTest(unittest.TestCase):
    def test_evaluation_reports_all_ranking_metrics(self):
        concepts = [
            {
                "code": "C3262",
                "name": "Neoplasm",
                "terminology": "ncit",
                "version": "26.06e",
                "definitions": [{"definition": "A tumor."}],
                "synonyms": [{"name": "Tumor"}],
                "properties": [],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_path:
            index = LocalIndex(tmp_path)
            provider = HashingEmbeddingProvider()
            index.upsert_concepts(concepts, "2026-06-29", provider)

            results = evaluate_retrieval(
                index,
                provider,
                gold_queries=[GoldQuery("tumor", ["C3262"])],
            )

            self.assertEqual([result.mode for result in results], ["bm25", "vector", "hybrid"])
            for result in results:
                self.assertEqual(result.query_count, 1)
                self.assertEqual(result.hit_at_1, 1.0)
                self.assertEqual(result.hit_at_5, 1.0)
                self.assertEqual(result.mean_reciprocal_rank, 1.0)
                self.assertEqual(result.to_dict()["mode"], result.mode)


if __name__ == "__main__":
    unittest.main()
