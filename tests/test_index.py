import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nci_si_mcp.embeddings import EmbeddingProvider, HashingEmbeddingProvider
from nci_si_mcp.errors import IndexCompatibilityError
from nci_si_mcp.evs import normalize_concept
from nci_si_mcp.index import SCHEMA_VERSION, LocalIndex, concept_search_text

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

    def test_schema_migration_and_fts_search(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            index = LocalIndex(tmp_path)
            provider = HashingEmbeddingProvider()
            index.upsert_concepts(RAW_CONCEPTS, "2026-06-29", provider)

            with index._connect() as conn:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                fts_count = conn.execute("SELECT COUNT(*) FROM concepts_fts").fetchone()[0]
                lsh_count = conn.execute("SELECT COUNT(*) FROM vector_lsh").fetchone()[0]

            self.assertEqual(version, SCHEMA_VERSION)
            self.assertEqual(fts_count, len(RAW_CONCEPTS))
            self.assertEqual(lsh_count, len(RAW_CONCEPTS) * 4)
            hits = index.search("tumor", provider, mode="bm25")
            self.assertEqual(hits[0].concept.code, "C3262")

    def test_legacy_database_is_backfilled_during_migration(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            db_path = Path(tmp_path) / "nci_si.sqlite3"
            provider = HashingEmbeddingProvider()
            concept = normalize_concept(
                RAW_CONCEPTS[0],
                release_date="2026-06-29",
                source="active_cache",
            )
            search_text = concept_search_text(concept)
            vector = provider.embed([search_text])[0]
            manifest = {
                "terminology": "ncit",
                "release_version": "26.06e",
                "release_date": "2026-06-29",
                "embedding_provider": provider.name,
                "embedding_model": provider.model,
                "concept_count": 1,
                "built_at": "2026-07-10T00:00:00Z",
                "index_path": str(db_path),
                "active": True,
            }
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute(
                    "CREATE TABLE manifests (release_version TEXT PRIMARY KEY, payload TEXT, active INTEGER)"
                )
                conn.execute(
                    """
                    CREATE TABLE concepts (
                        release_version TEXT,
                        code TEXT,
                        payload TEXT,
                        search_text TEXT,
                        vector TEXT,
                        PRIMARY KEY (release_version, code)
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO manifests VALUES (?, ?, 1)",
                    ("26.06e", json.dumps(manifest)),
                )
                conn.execute(
                    "INSERT INTO concepts VALUES (?, ?, ?, ?, ?)",
                    (
                        "26.06e",
                        concept.code,
                        json.dumps(concept.to_dict(include_raw=True)),
                        search_text,
                        json.dumps(vector),
                    ),
                )

            index = LocalIndex(Path(tmp_path))

            with index._connect() as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM concepts_fts").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM vector_lsh").fetchone()[0],
                    4,
                )
            self.assertEqual(index.search("kinase", provider)[0].concept.code, "C40704")

    def test_release_mismatch_is_rejected_before_activation(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            index = LocalIndex(tmp_path)

            with self.assertRaises(IndexCompatibilityError):
                index.upsert_concepts(
                    RAW_CONCEPTS,
                    "2026-06-29",
                    HashingEmbeddingProvider(),
                    expected_release_version="26.07a",
                )

            self.assertIsNone(index.get_active_manifest())

    def test_failed_index_transaction_preserves_prior_active_release(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            index = LocalIndex(tmp_path)
            provider = HashingEmbeddingProvider()
            index.upsert_concepts(RAW_CONCEPTS, "2026-06-29", provider)
            next_release = [dict(RAW_CONCEPTS[0], version="26.07a")]
            with index._connect() as conn:
                conn.execute("DROP TABLE concepts_fts")

            with self.assertRaises(sqlite3.OperationalError):
                index.upsert_concepts(next_release, "2026-07-06", provider)

            self.assertEqual(index.get_active_manifest().release_version, "26.06e")
            with index._connect() as conn:
                next_release_count = conn.execute(
                    "SELECT COUNT(*) FROM concepts WHERE release_version = ?",
                    ("26.07a",),
                ).fetchone()[0]
            self.assertEqual(next_release_count, 0)

    def test_search_rejects_embedding_provider_mismatch(self):
        class AlternateProvider(HashingEmbeddingProvider):
            def __init__(self):
                super().__init__()
                self.name = "alternate"
                self.model = "alternate-128"

        with tempfile.TemporaryDirectory() as tmp_path:
            index = LocalIndex(tmp_path)
            index.upsert_concepts(
                RAW_CONCEPTS,
                "2026-06-29",
                HashingEmbeddingProvider(),
            )

            with self.assertRaises(IndexCompatibilityError):
                index.search("tumor", AlternateProvider(), mode="vector")

    def test_bm25_mode_does_not_compute_embeddings(self):
        class NoEmbedProvider(EmbeddingProvider):
            name = "hashing"
            model = "hashing-128"

            def embed(self, texts):
                raise AssertionError("BM25-only search must not embed the query")

        with tempfile.TemporaryDirectory() as tmp_path:
            index = LocalIndex(tmp_path)
            index.upsert_concepts(
                RAW_CONCEPTS,
                "2026-06-29",
                HashingEmbeddingProvider(),
            )

            hits = index.search("tumor", NoEmbedProvider(), mode="bm25")

            self.assertEqual(hits[0].concept.code, "C3262")


if __name__ == "__main__":
    unittest.main()
