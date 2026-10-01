import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fakes import concept
from nci_si_mcp.embeddings import EmbeddingProvider, HashingEmbeddingProvider
from nci_si_mcp.errors import (
    IndexBuildError,
    IndexCompatibilityError,
    IndexStorageError,
    InputValidationError,
    NoActiveIndexError,
)
from nci_si_mcp.evs import normalize_concept
from nci_si_mcp.index import (
    LSH_BANDS,
    SCHEMA_VERSION,
    LocalIndex,
    concept_search_text,
    vector_lsh_buckets,
)
from nci_si_mcp.retrieval import min_max_normalize

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
WORDS = [
    "alpha",
    "bravo",
    "carbon",
    "delta",
    "ember",
    "fjord",
    "garnet",
    "harbor",
    "indigo",
    "jasper",
]


def synthetic_concepts(count):
    """Concepts whose names are distinct three-word combinations."""

    return [
        concept(
            f"C{number}",
            " ".join(WORDS[(number // step) % 10] + str(step) for step in (1, 10, 100)),
        )
        for number in range(count)
    ]


class RenamedProvider(HashingEmbeddingProvider):
    def __init__(self, name="hashing", model="hashing-128", dimensions=128):
        super().__init__(dimensions)
        self.name = name
        self.model = model


class IndexTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.provider = HashingEmbeddingProvider()

    def build(self, concepts=RAW_CONCEPTS, date="2026-06-29", provider=None, **options):
        index = LocalIndex(self.path)
        index.upsert_concepts(concepts, date, provider or self.provider, **options)
        return index

    def counts(self, index):
        with index._connect() as conn:
            return tuple(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
                for table in ("concepts", "concepts_fts", "vector_lsh", "manifests")
            )


class UpsertTest(IndexTestCase):
    def test_upsert_activates_the_release_and_caches_concepts_with_provenance(self):
        index = LocalIndex(self.path)
        manifest = index.upsert_concepts(RAW_CONCEPTS, "2026-06-29", self.provider)

        self.assertEqual(manifest.release_version, "26.06e")
        self.assertEqual(manifest.concept_count, 2)
        self.assertEqual(manifest.embedding_dimensions, 128)
        self.assertEqual(index.get_active_manifest(), manifest)
        self.assertEqual(self.counts(index), (2, 2, 2 * LSH_BANDS, 1))

        cached = index.get_concept("C3262")
        self.assertEqual(cached.source, "active_cache")
        self.assertEqual(cached.release_version, "26.06e")
        self.assertEqual(cached.release_date, "2026-06-29")
        self.assertNotIn("raw", cached.to_dict())
        self.assertIn("raw", cached.to_dict(include_raw=True))
        self.assertIsNone(index.get_concept("C999"))

    def test_cached_concept_keeps_the_time_it_was_fetched(self):
        index = self.build()

        first = index.get_concept("C3262").retrieved_at
        second = index.get_concept("C3262").retrieved_at

        self.assertEqual(first, second)
        self.assertEqual(index.search("tumor", self.provider)[0].concept.retrieved_at, first)

    def test_reindexing_a_code_replaces_its_rows(self):
        index = self.build()

        manifest = index.upsert_concepts(
            [dict(RAW_CONCEPTS[1], name="Renamed Growth", synonyms=[], definitions=[])],
            "2026-06-29",
            self.provider,
        )

        self.assertEqual(manifest.concept_count, 2)
        self.assertEqual(self.counts(index), (2, 2, 2 * LSH_BANDS, 1))
        self.assertEqual(index.get_concept("C3262").preferred_name, "Renamed Growth")
        for mode in ("bm25", "vector"):
            hits = index.search("renamed", self.provider, mode=mode)
            self.assertEqual(hits[0].concept.code, "C3262", mode)
            self.assertEqual(hits[0].concept.preferred_name, "Renamed Growth")
        self.assertGreater(hits[0].score, hits[1].score)
        self.assertEqual(index.search("tumor", self.provider, mode="bm25"), [])

    def test_upsert_holds_the_write_lock_while_it_checks_compatibility(self):
        index = self.build()
        observed = []
        read_manifest = LocalIndex._active_manifest

        def competing_writer(conn):
            other = sqlite3.connect(str(index.db_path), timeout=0)
            try:
                other.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                observed.append(str(error))
            finally:
                other.close()
            return read_manifest(conn)

        with patch.object(LocalIndex, "_active_manifest", staticmethod(competing_writer)):
            index.upsert_concepts([RAW_CONCEPTS[0]], "2026-06-29", self.provider)

        self.assertEqual(observed, ["database is locked"])

    def test_indexing_another_release_replaces_the_previous_one(self):
        index = self.build()

        with self.assertLogs("nci_si_mcp.index", level="INFO"):
            manifest = index.upsert_concepts(
                [dict(RAW_CONCEPTS[1], version="26.07d")], "2026-07-27", self.provider
            )

        self.assertEqual(manifest.release_version, "26.07d")
        self.assertEqual(manifest.concept_count, 1)
        self.assertEqual(self.counts(index), (1, 1, LSH_BANDS, 1))
        self.assertIsNone(index.get_concept("C40704"))
        self.assertEqual(index.get_concept("C3262").release_version, "26.07d")
        self.assertEqual(index.search("kinase", self.provider, mode="bm25"), [])

    def test_release_mismatch_is_rejected_and_keeps_the_previous_release(self):
        index = self.build()
        before = index.get_active_manifest()

        with self.assertRaises(IndexCompatibilityError):
            index.upsert_concepts(
                [dict(RAW_CONCEPTS[0], version="26.07a")],
                "2026-07-06",
                self.provider,
                expected_release_version="26.07d",
            )

        self.assertEqual(index.get_active_manifest(), before)
        self.assertEqual(self.counts(index), (2, 2, 2 * LSH_BANDS, 1))

    def test_unusable_batches_are_rejected(self):
        index = LocalIndex(self.path)
        batches = {
            "empty": [],
            "no version": [dict(RAW_CONCEPTS[0], version="")],
            "no code": [dict(RAW_CONCEPTS[0], code="")],
            "mixed versions": [RAW_CONCEPTS[0], dict(RAW_CONCEPTS[1], version="26.07a")],
        }
        for label, batch in batches.items():
            with self.subTest(label), self.assertRaises(IndexBuildError):
                index.upsert_concepts(batch, "2026-06-29", self.provider)
        self.assertIsNone(index.get_active_manifest())

    def test_a_code_repeated_in_one_batch_is_indexed_once(self):
        index = LocalIndex(self.path)

        manifest = index.upsert_concepts(
            [RAW_CONCEPTS[1], dict(RAW_CONCEPTS[1], name="Later Name")], "2026-06-29", self.provider
        )

        self.assertEqual(manifest.concept_count, 1)
        self.assertEqual(self.counts(index), (1, 1, LSH_BANDS, 1))
        self.assertEqual(index.get_concept("C3262").preferred_name, "Later Name")

    def test_count_and_search_ignore_rows_of_an_inactive_release(self):
        index = self.build()
        stale = normalize_concept(
            concept("C9999", "Tumor Marker", version="26.99z"), None, "active_cache"
        )
        text = concept_search_text(stale)
        with index._connect() as conn:
            conn.execute(
                "INSERT INTO concepts VALUES ('26.99z', 'C9999', ?, ?, ?)",
                (
                    json.dumps(stale.to_dict(include_raw=True)),
                    text,
                    json.dumps(self.provider.embed([text])[0]),
                ),
            )
            conn.execute("INSERT INTO concepts_fts VALUES ('26.99z', 'C9999', ?)", (text,))

        manifest = index.upsert_concepts([RAW_CONCEPTS[0]], "2026-06-29", self.provider)

        self.assertEqual(manifest.concept_count, 2)
        self.assertIsNone(index.get_concept("C9999"))
        for mode in ("bm25", "vector", "hybrid"):
            hits = index.search("tumor marker", self.provider, limit=10, mode=mode)
            self.assertNotIn("C9999", [hit.concept.code for hit in hits], mode)
            self.assertEqual({hit.concept.release_version for hit in hits}, {"26.06e"}, mode)

    def test_failed_write_rolls_back_every_table_and_keeps_the_previous_release(self):
        index = self.build()
        before = index.get_active_manifest()
        with index._connect() as conn:
            conn.execute(
                """
                CREATE TRIGGER fail_manifest BEFORE INSERT ON manifests
                BEGIN SELECT RAISE(ABORT, 'disk full'); END
                """
            )

        with self.assertRaises(sqlite3.IntegrityError):
            index.upsert_concepts(
                [dict(RAW_CONCEPTS[0], version="26.07d")], "2026-07-27", self.provider
            )

        self.assertEqual(index.get_active_manifest(), before)
        self.assertEqual(self.counts(index), (2, 2, 2 * LSH_BANDS, 1))
        self.assertEqual(index.get_concept("C40704").release_version, "26.06e")

    def test_a_different_embedding_space_cannot_join_the_active_release(self):
        providers = {
            "provider": RenamedProvider(name="other"),
            "model": RenamedProvider(model="hashing-v2"),
            "dimensions": RenamedProvider(dimensions=64),
        }
        for label, provider in providers.items():
            with self.subTest(label):
                index = self.build()
                before = index.get_active_manifest()
                with self.assertRaises(IndexCompatibilityError) as raised:
                    index.upsert_concepts([RAW_CONCEPTS[0]], "2026-06-29", provider)
                self.assertIn("nci_si.sqlite3", str(raised.exception))
                self.assertEqual(index.get_active_manifest(), before)
                (self.path / "nci_si.sqlite3").unlink()

    def test_dimension_change_is_rejected_when_the_manifest_predates_dimensions(self):
        index = self.build()
        with index._connect() as conn:
            payload = json.loads(conn.execute("SELECT payload FROM manifests").fetchone()[0])
            del payload["embedding_dimensions"]
            conn.execute("UPDATE manifests SET payload = ?", (json.dumps(payload),))

        with self.assertRaises(IndexCompatibilityError):
            index.upsert_concepts([RAW_CONCEPTS[0]], "2026-06-29", RenamedProvider(dimensions=64))

    def test_provider_returning_wrong_vectors_is_rejected(self):
        class ShortProvider(HashingEmbeddingProvider):
            def embed(self, texts):
                return super().embed(texts)[:-1]

        class RaggedProvider(HashingEmbeddingProvider):
            def embed(self, texts):
                vectors = super().embed(texts)
                return [vectors[0][:-1], *vectors[1:]]

        for provider in (ShortProvider(), RaggedProvider()):
            with self.subTest(type(provider).__name__), self.assertRaises(IndexCompatibilityError):
                LocalIndex(self.path).upsert_concepts(RAW_CONCEPTS, "2026-06-29", provider)


class StorageTest(IndexTestCase):
    def test_unusable_database_is_a_storage_error_naming_the_file(self):
        index = self.build()
        index.db_path.write_bytes(b"not a database" * 100)

        for call in (index.get_active_manifest, lambda: LocalIndex(self.path)):
            with self.assertRaises(IndexStorageError) as raised:
                call()
            self.assertIn(str(index.db_path), str(raised.exception))

    def test_database_that_cannot_be_opened_is_a_storage_error_naming_the_file(self):
        (self.path / "nci_si.sqlite3").mkdir()

        with self.assertRaises(IndexStorageError) as raised:
            LocalIndex(self.path)

        self.assertIn(str(self.path / "nci_si.sqlite3"), str(raised.exception))

    def test_search_reads_one_snapshot_while_the_release_is_replaced(self):
        index = self.build()
        read_manifest = LocalIndex._active_manifest
        replaced = []

        def replace_release_then_read(conn):
            manifest = read_manifest(conn)
            if not replaced:
                replaced.append(True)
                LocalIndex(self.path).upsert_concepts(
                    [concept("C9999", "Tumor Marker", version="26.07d")],
                    "2026-07-27",
                    self.provider,
                )
            return manifest

        with patch.object(LocalIndex, "_active_manifest", staticmethod(replace_release_then_read)):
            hits = index.search("tumor", self.provider, mode="hybrid")

        self.assertEqual({hit.concept.release_version for hit in hits}, {"26.06e"})
        self.assertEqual(index.get_active_manifest().release_version, "26.07d")

    def test_connections_are_closed_after_use(self):
        index = self.build()

        with index._connect() as conn:
            conn.execute("SELECT 1")

        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_reopening_preserves_the_index(self):
        index = self.build()
        manifest = index.get_active_manifest()

        reopened = LocalIndex(self.path)

        self.assertEqual(reopened.get_active_manifest(), manifest)
        self.assertEqual(self.counts(reopened), (2, 2, 2 * LSH_BANDS, 1))
        self.assertEqual(
            reopened.search("tumor", self.provider, mode="bm25")[0].concept.code, "C3262"
        )

    def test_opening_a_current_database_does_not_need_the_write_lock(self):
        self.build()
        writer = sqlite3.connect(str(self.path / "nci_si.sqlite3"), timeout=0)
        self.addCleanup(writer.close)
        writer.execute("BEGIN IMMEDIATE")

        connect = sqlite3.connect
        with patch("nci_si_mcp.index.sqlite3.connect", lambda path: connect(path, timeout=0)):
            manifest = LocalIndex(self.path).get_active_manifest()

        self.assertEqual(manifest.release_version, "26.06e")

    def test_newer_schema_is_refused(self):
        index = self.build()
        with index._connect() as conn:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

        with self.assertRaises(IndexCompatibilityError):
            LocalIndex(self.path)

    def test_manifest_with_unknown_keys_still_loads(self):
        index = self.build()
        with index._connect() as conn:
            payload = json.loads(conn.execute("SELECT payload FROM manifests").fetchone()[0])
            payload["added_by_a_later_version"] = 1
            conn.execute("UPDATE manifests SET payload = ?", (json.dumps(payload),))

        self.assertEqual(index.get_active_manifest().release_version, "26.06e")


class MigrationTest(IndexTestCase):
    def legacy_database(self, releases):
        """Write a database in the layout of the first prototype (schema 0)."""

        db_path = self.path / "nci_si.sqlite3"
        vectors = {}
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "CREATE TABLE manifests "
                "(release_version TEXT PRIMARY KEY, payload TEXT, active INTEGER)"
            )
            conn.execute(
                """
                CREATE TABLE concepts (
                    release_version TEXT, code TEXT, payload TEXT, search_text TEXT, vector TEXT,
                    PRIMARY KEY (release_version, code)
                )
                """
            )
            for release_version, raw in releases:
                item = normalize_concept(
                    dict(raw, version=release_version), release_date=None, source="active_cache"
                )
                search_text = concept_search_text(item)
                vectors[release_version] = self.provider.embed([search_text])[0]
                manifest = {
                    "terminology": "ncit",
                    "release_version": release_version,
                    "release_date": None,
                    "embedding_provider": self.provider.name,
                    "embedding_model": self.provider.model,
                    "concept_count": 1,
                    "built_at": "2026-07-10T00:00:00Z",
                    "index_path": str(db_path),
                    "active": True,
                }
                conn.execute(
                    "INSERT INTO manifests VALUES (?, ?, 1)",
                    (release_version, json.dumps(manifest)),
                )
                conn.execute(
                    "INSERT INTO concepts VALUES (?, ?, ?, ?, ?)",
                    (
                        release_version,
                        item.code,
                        json.dumps(item.to_dict(include_raw=True)),
                        search_text,
                        json.dumps(vectors[release_version]),
                    ),
                )
        conn.close()
        return vectors

    def test_legacy_database_is_backfilled(self):
        vectors = self.legacy_database([("26.06e", RAW_CONCEPTS[0])])

        index = LocalIndex(self.path)

        with index._connect() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM concepts_fts").fetchone()[0], 1)
            buckets = conn.execute("SELECT band, bucket FROM vector_lsh ORDER BY band").fetchall()
        self.assertEqual([tuple(row) for row in buckets], vector_lsh_buckets(vectors["26.06e"]))
        self.assertIsNone(index.get_active_manifest().embedding_dimensions)
        for mode in ("bm25", "vector"):
            self.assertEqual(
                index.search("kinase", self.provider, mode=mode)[0].concept.code, "C40704"
            )

    def test_several_active_manifests_are_reduced_to_one(self):
        self.legacy_database([("26.05d", RAW_CONCEPTS[0]), ("26.06e", RAW_CONCEPTS[1])])

        index = LocalIndex(self.path)

        self.assertEqual(index.get_active_manifest().release_version, "26.06e")
        self.assertEqual(self.counts(index), (1, 1, LSH_BANDS, 1))
        self.assertIsNone(index.get_concept("C40704"))
        for mode in ("bm25", "vector", "hybrid"):
            hits = index.search("kinase inhibition tumor", self.provider, mode=mode)
            self.assertEqual([hit.concept.code for hit in hits], ["C3262"], mode)

    def test_releases_left_behind_by_earlier_versions_are_dropped_with_a_warning(self):
        index = self.build()
        with index._connect() as conn:
            conn.execute(
                "INSERT INTO concepts "
                "SELECT '26.05d', code, payload, search_text, vector FROM concepts"
            )
            conn.execute(
                "INSERT INTO concepts_fts SELECT '26.05d', code, search_text FROM concepts_fts"
            )
            conn.execute(
                "INSERT INTO vector_lsh SELECT '26.05d', code, band, bucket FROM vector_lsh"
            )
            conn.execute("INSERT INTO manifests VALUES ('26.05d', '{}', 0)")
            conn.execute("PRAGMA user_version = 3")

        with self.assertLogs("nci_si_mcp.index", level="WARNING") as logs:
            migrated = LocalIndex(self.path)

        self.assertIn("26.05d", logs.output[0])
        self.assertEqual(self.counts(migrated), (2, 2, 2 * LSH_BANDS, 1))
        self.assertEqual(migrated.get_active_manifest().release_version, "26.06e")

    def test_schema_one_database_gains_the_search_rows(self):
        index = self.build()
        with index._connect() as conn:
            conn.execute("DELETE FROM concepts_fts")
            conn.execute("PRAGMA user_version = 1")

        migrated = LocalIndex(self.path)

        self.assertEqual(self.counts(migrated), (2, 2, 2 * LSH_BANDS, 1))
        hits = migrated.search("kinase", self.provider, mode="bm25")
        self.assertEqual([hit.concept.code for hit in hits], ["C40704"])

    def test_schema_two_database_gains_the_lsh_table(self):
        index = self.build()
        with index._connect() as conn:
            conn.execute("DROP TABLE vector_lsh")
            conn.execute("PRAGMA user_version = 2")

        migrated = LocalIndex(self.path)

        self.assertEqual(self.counts(migrated), (2, 2, 2 * LSH_BANDS, 1))


class SearchTest(IndexTestCase):
    def test_hybrid_search_ranks_and_reports_score_components(self):
        index = self.build()

        hits = index.search("kinase inhibition", self.provider, limit=2, mode="hybrid")

        self.assertEqual([hit.concept.code for hit in hits], ["C40704", "C3262"])
        self.assertEqual([hit.rank for hit in hits], [1, 2])
        self.assertEqual(hits[0].score_components, {"bm25": 1.0, "vector": 1.0})
        self.assertEqual(hits[0].concept.source, "active_cache")
        self.assertNotIn("raw", hits[0].to_dict()["concept"])
        self.assertIn("raw", hits[0].to_dict(include_raw=True)["concept"])

    def test_scores_are_min_max_normalized(self):
        self.assertEqual(
            min_max_normalize({"a": 2.0, "b": 3.0, "c": 4.0}), {"a": 0.0, "b": 0.5, "c": 1.0}
        )
        self.assertEqual(min_max_normalize({"a": -1.0, "b": 0.0}), {"a": 0.0, "b": 1.0})
        self.assertEqual(min_max_normalize({"a": 2.0, "b": 2.0}), {"a": 1.0, "b": 1.0})
        self.assertEqual(min_max_normalize({}), {})

    def test_concept_without_a_matching_term_has_a_zero_bm25_component(self):
        index = self.build()

        hits = index.search("kinase inhibition", self.provider, mode="hybrid")

        self.assertEqual([hit.concept.code for hit in hits], ["C40704", "C3262"])
        self.assertEqual(hits[1].score_components, {"bm25": 0.0, "vector": 0.0})
        self.assertEqual((hits[0].score, hits[1].score), (1.0, 0.0))

    def test_hybrid_score_weighs_bm25_and_vector(self):
        index = self.build(synthetic_concepts(30))

        hits = index.search("alpha1 bravo10 alpha100", self.provider, limit=30, mode="hybrid")

        self.assertEqual(len(hits), 30)
        for hit in hits:
            components = hit.score_components
            self.assertAlmostEqual(
                hit.score, 0.55 * components["bm25"] + 0.45 * components["vector"]
            )
        self.assertTrue(
            any(h.score_components["bm25"] != h.score_components["vector"] for h in hits)
        )
        self.assertEqual(
            [hit.score for hit in hits], sorted((hit.score for hit in hits), reverse=True)
        )

    def test_search_text_holds_code_name_synonyms_definitions_types_and_sources(self):
        raw = dict(
            RAW_CONCEPTS[1],
            properties=[
                {"type": "Semantic_Type", "value": "Neoplastic Process"},
                {"type": "Contributing_Source", "value": "GDC"},
            ],
        )

        text = concept_search_text(normalize_concept(raw, release_date=None, source="active_cache"))

        self.assertEqual(
            text, "C3262 Neoplasm Tumor A benign or malignant tissue growth. Neoplastic Process GDC"
        )

    def test_stored_formats_are_stable(self):
        # Stored vectors and LSH buckets depend on these; a change needs a migration.
        vector = HashingEmbeddingProvider(8).embed(["tumor growth factor tumor"])[0]
        self.assertEqual(
            [round(value, 6) for value in vector],
            [-0.316228, 0.0, 0.0, 0.0, 0.0, -0.948683, 0.0, 0.0],
        )
        self.assertEqual(
            vector_lsh_buckets(self.provider.embed(["neoplasm tumor growth"])[0]),
            [(0, 235), (1, 12), (2, 219), (3, 64)],
        )

    def test_search_without_an_index_is_refused(self):
        with self.assertRaises(NoActiveIndexError):
            LocalIndex(self.path).search("tumor", self.provider)

    def test_search_validates_its_arguments(self):
        index = self.build()
        for arguments in ((" ", 10, "hybrid"), ("tumor", 0, "hybrid"), ("tumor", 10, "fuzzy")):
            with self.subTest(arguments=arguments), self.assertRaises(InputValidationError):
                index.search(arguments[0], self.provider, limit=arguments[1], mode=arguments[2])

    def test_search_rejects_a_runtime_from_another_embedding_space(self):
        index = self.build()
        providers = {
            "provider": RenamedProvider(name="other"),
            "model": RenamedProvider(model="hashing-v2"),
            "dimensions": RenamedProvider(dimensions=64),
        }
        for label, provider in providers.items():
            with self.subTest(label), self.assertRaises(IndexCompatibilityError):
                index.search("tumor", provider, mode="vector")

    def test_bm25_search_also_refuses_another_embedding_space(self):
        index = self.build()

        with self.assertRaises(IndexCompatibilityError):
            index.search("tumor", RenamedProvider(model="hashing-v2"), mode="bm25")

    def test_query_vector_count_is_checked(self):
        class TwoVectors(RenamedProvider):
            def embed(self, texts):
                return super().embed(texts) * 2

        index = self.build()

        with self.assertRaises(IndexCompatibilityError):
            index.search("tumor", TwoVectors(), mode="vector")

    def test_stored_vectors_of_another_width_are_detected_on_a_legacy_manifest(self):
        index = self.build()
        with index._connect() as conn:
            payload = json.loads(conn.execute("SELECT payload FROM manifests").fetchone()[0])
            del payload["embedding_dimensions"]
            conn.execute("UPDATE manifests SET payload = ?", (json.dumps(payload),))

        with self.assertRaises(IndexCompatibilityError):
            index.search("tumor", RenamedProvider(dimensions=64), mode="vector")

    def test_bm25_mode_does_not_compute_embeddings(self):
        class NoEmbedProvider(EmbeddingProvider):
            name = "hashing"
            model = "hashing-128"

            def embed(self, texts):
                raise AssertionError("BM25-only search must not embed the query")

        index = self.build()

        hits = index.search("tumor", NoEmbedProvider(), mode="bm25")

        self.assertEqual(hits[0].concept.code, "C3262")
        self.assertEqual(hits[0].score_components["vector"], 0.0)

    def test_bm25_matches_names_with_diacritics(self):
        index = self.build([concept("C26883", "Sjögren Syndrome"), concept("C3262", "Neoplasm")])

        for query in ("Sjögren", "sjogren", "SJÖGREN syndrome"):
            with self.subTest(query):
                hits = index.search(query, self.provider, mode="bm25")
                self.assertEqual(hits[0].concept.code, "C26883")

    def test_fts_syntax_in_a_query_is_inert(self):
        index = self.build()

        for query in ('tumor" OR (', "NEAR(a b)", "C3262*", "non-small", "tumor AND NOT growth"):
            with self.subTest(query):
                index.search(query, self.provider, mode="bm25")
        self.assertEqual(index.search("!!!", self.provider, mode="bm25"), [])

    def test_vector_search_is_exact_when_the_release_fits_the_scan_limit(self):
        concepts = synthetic_concepts(200)
        index = self.build(concepts)

        for raw in concepts[::10]:
            hits = index.search(raw["name"], self.provider, limit=5, mode="vector")
            self.assertEqual(len(hits), 5)
            self.assertEqual(hits[0].concept.code, raw["code"], raw["name"])

    def test_hybrid_search_fills_the_limit_from_the_whole_small_index(self):
        index = self.build(synthetic_concepts(30))

        hits = index.search("alpha1 alpha10 alpha100", self.provider, limit=20, mode="hybrid")

        self.assertEqual(len(hits), 20)
        self.assertEqual(hits[0].concept.code, "C0")

    def test_large_release_gives_bm25_candidates_a_vector_score(self):
        concepts = synthetic_concepts(60)
        index = self.build(concepts)

        with (
            patch("nci_si_mcp.index.EXACT_VECTOR_SCAN_LIMIT", 20),
            patch("nci_si_mcp.index.MAX_VECTOR_CANDIDATES", 20),
        ):
            # The stored search text of a concept lands in all of its own buckets.
            own_text = concept_search_text(
                normalize_concept(concepts[7], release_date=None, source="active_cache")
            )
            vector_hits = index.search(own_text, self.provider, limit=60, mode="vector")
            hybrid_hits = index.search("garnet1", self.provider, limit=60, mode="hybrid")

        self.assertEqual(vector_hits[0].concept.code, "C7")
        bm25_matches = {f"C{number}" for number in range(6, 60, 10)}
        by_code = {hit.concept.code: hit for hit in hybrid_hits}
        self.assertTrue(bm25_matches <= set(by_code))
        # A BM25 candidate is given a vector score too, not only its BM25 score.
        self.assertTrue(any(by_code[code].score_components["vector"] > 0 for code in bm25_matches))

    def test_large_release_scores_at_most_the_candidate_cap(self):
        concepts = synthetic_concepts(200)
        index = self.build(concepts)
        own_text = concept_search_text(
            normalize_concept(concepts[7], release_date=None, source="active_cache")
        )

        with (
            patch("nci_si_mcp.index.EXACT_VECTOR_SCAN_LIMIT", 20),
            patch("nci_si_mcp.index.MAX_VECTOR_CANDIDATES", 5),
            patch("nci_si_mcp.index.MAX_FTS_CANDIDATES", 3),
        ):
            vector_hits = index.search(own_text, self.provider, limit=100, mode="vector")
            # Three BM25 candidates leave room for two LSH candidates.
            hybrid_hits = index.search(
                "C7 harbor1 alpha10 alpha100", self.provider, limit=100, mode="hybrid"
            )

        # Of the many concepts sharing a band, those sharing the most are kept.
        self.assertEqual(len(vector_hits), 5)
        self.assertEqual(vector_hits[0].concept.code, "C7")
        self.assertLessEqual(len(hybrid_hits), 5)
        self.assertGreater(len(hybrid_hits), 3)

    def test_zero_projection_falls_in_the_set_bit(self):
        self.assertEqual(vector_lsh_buckets([0.0] * 8), [(0, 255), (1, 255), (2, 255), (3, 255)])

    def test_search_only_returns_the_active_release(self):
        index = self.build()
        index.upsert_concepts(
            [dict(RAW_CONCEPTS[1], version="26.07d")], "2026-07-27", self.provider
        )

        for mode in ("bm25", "vector", "hybrid"):
            with self.subTest(mode):
                hits = index.search("kinase tumor", self.provider, mode=mode)
                self.assertEqual({hit.concept.release_version for hit in hits}, {"26.07d"})

    def test_bm25_scores_of_the_top_hits_do_not_depend_on_a_small_limit(self):
        # Thirty concepts match "kinase", each with its own document length and so its own score.
        concepts = [
            concept(f"C{number}", "kinase " + " ".join(f"filler{n}" for n in range(number)))
            for number in range(1, 31)
        ]
        index = self.build(concepts)

        every = index.search("kinase", self.provider, limit=30, mode="bm25")
        few = index.search("kinase", self.provider, limit=2, mode="bm25")

        self.assertEqual(len({hit.score for hit in every}), 30)
        self.assertEqual(
            [(hit.concept.code, hit.score) for hit in few],
            [(hit.concept.code, hit.score) for hit in every[:2]],
        )


if __name__ == "__main__":
    unittest.main()
