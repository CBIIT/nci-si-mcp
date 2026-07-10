import tempfile
import unittest

from nci_si_mcp.embeddings import HashingEmbeddingProvider
from nci_si_mcp.index import LocalIndex


RAW_CONCEPTS = [
    {
        "code": "C40704",
        "name": "Receptor Tyrosine Kinase Inhibition",
        "terminology": "ncit",
        "version": "26.06e",
        "definitions": [{"definition": "Inhibition of receptor tyrosine kinase activity."}],
        "synonyms": [{"name": "RTK Inhibition", "source": "NCI"}],
        "properties": [{"type": "Semantic_Type", "value": "Molecular Function"}],
    },
    {
        "code": "C3262",
        "name": "Neoplasm",
        "terminology": "ncit",
        "version": "26.06e",
        "definitions": [{"definition": "A benign or malignant tissue growth."}],
        "synonyms": [{"name": "Tumor", "source": "NCI"}],
        "properties": [{"type": "Semantic_Type", "value": "Neoplastic Process"}],
    },
]


class IndexTest(unittest.TestCase):
    def test_index_search_and_cache_provenance(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            index = LocalIndex(tmp_path)
            provider = HashingEmbeddingProvider()
            manifest = index.upsert_concepts(RAW_CONCEPTS, "2026-06-29", provider)

            self.assertEqual(manifest.release_version, "26.06e")
            self.assertEqual(manifest.concept_count, 2)

            cached = index.get_concept("C3262")
            self.assertIsNotNone(cached)
            self.assertEqual(cached.source, "active_cache")
            self.assertEqual(cached.release_version, "26.06e")
            self.assertNotIn("raw", cached.to_dict())
            self.assertIn("raw", cached.to_dict(include_raw=True))

            hits = index.search("kinase inhibition", provider, limit=2, mode="hybrid")
            self.assertEqual(hits[0].concept.code, "C40704")
            self.assertEqual(hits[0].concept.source, "active_cache")
            self.assertIn("bm25", hits[0].score_components)
            self.assertIn("vector", hits[0].score_components)
            self.assertNotIn("raw", hits[0].to_dict()["concept"])
            self.assertIn("raw", hits[0].to_dict(include_raw=True)["concept"])


if __name__ == "__main__":
    unittest.main()
