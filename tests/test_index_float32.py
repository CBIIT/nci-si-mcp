"""Existing builds remain searchable after the vector storage migration."""

import json
import sqlite3
import struct
from contextlib import closing
from unittest.mock import patch

from fakes import concept
from nci_si_mcp.embeddings import HashingEmbeddingProvider
from nci_si_mcp.errors import IndexCompatibilityError, IndexStorageError
from nci_si_mcp.evs import normalize_concept
from nci_si_mcp.index import LocalIndex
from nci_si_mcp.index_storage import SCHEMA_VERSION, concept_fields, create_fts, fts_table
from nci_si_mcp.models import IndexManifest
from test_index import IndexTestCase


def schema_five(conn):
    statements = (
        "CREATE TABLE manifests (build_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
        "active INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'complete')",
        "CREATE UNIQUE INDEX manifests_single_active ON manifests(active) WHERE active = 1",
        "CREATE TABLE concepts (build_id TEXT, code TEXT, payload TEXT, name_key TEXT, "
        "PRIMARY KEY (build_id, code))",
        "CREATE INDEX concepts_name ON concepts(build_id, name_key)",
        "CREATE TABLE fields (id INTEGER PRIMARY KEY, build_id TEXT, code TEXT, "
        "kind TEXT, text TEXT, vector TEXT)",
        "CREATE INDEX fields_concept ON fields(build_id, code)",
        "CREATE TABLE vector_lsh (field_id INTEGER, table_id INTEGER, bucket INTEGER)",
        "PRAGMA user_version = 5",
    )
    for statement in statements:
        conn.execute(statement)


class Float32MigrationTest(IndexTestCase):
    def old_build(self, conn, build, rows, active):
        manifest = IndexManifest(
            "ncit",
            "26.06e",
            None,
            self.provider.name,
            self.provider.model,
            len(rows),
            "2026-10-05T00:00:00Z",
            str(self.path / "nci_si.sqlite3"),
            self.provider.dimensions,
            active,
            build,
        )
        conn.execute(
            "INSERT INTO manifests VALUES (?, ?, ?, 'complete')",
            (build, json.dumps(manifest.to_dict()), int(active)),
        )
        create_fts(conn, build)
        for raw in rows:
            self.old_concept(conn, build, raw)
        return manifest

    def old_concept(self, conn, build, raw):
        item = normalize_concept(raw, release_date=None, source="active_cache")
        conn.execute(
            "INSERT INTO concepts VALUES (?, ?, ?, ?)",
            (build, item.code, json.dumps(item.to_stored()), item.preferred_name.lower()),
        )
        for kind, text in concept_fields(item):
            vector = self.provider.embed([text])[0]
            field = conn.execute(
                "INSERT INTO fields(build_id, code, kind, text, vector) VALUES (?, ?, ?, ?, ?)",
                (build, item.code, kind, text, json.dumps(vector)),
            )
            conn.execute(
                f"INSERT INTO {fts_table(build)}(rowid, code, kind, text) VALUES (?, ?, ?, ?)",  # noqa: S608
                (field.lastrowid, item.code, kind, text),
            )

    def test_schema_five_keeps_both_builds_status_fts_and_rankings(self):
        rows = [
            concept("C1", "Alpha", active=False, conceptStatus="retired-test"),
            concept("C2", "Beta", active=True, synonyms=[{"name": "Kinase"}]),
        ]
        with closing(sqlite3.connect(self.path / "nci_si.sqlite3")) as conn, conn:
            schema_five(conn)
            active = self.old_build(conn, "a" * 32, rows, True)
            older = self.old_build(conn, "b" * 32, [concept("C3", "Older", active=True)], False)
        index = LocalIndex(self.path)
        self.assertEqual(index.get_active_manifest(), active)
        self.assertEqual(
            {m.build_id for m in index.list_builds()}, {active.build_id, older.build_id}
        )
        self.assertEqual(index.get_concept("C1").raw, rows[0])
        for mode in ("bm25", "vector", "hybrid"):
            with self.subTest(mode=mode):
                hits = index.search("Kinase", self.provider, mode=mode)
                self.assertEqual((hits[0].concept.code, hits[0].matched_on), ("C2", "synonym"))
        retired, total, _ = index.search_page(
            "Kinase", self.provider, retired_status="retired-test"
        )
        self.assertEqual(([h.concept.code for h in retired], total), (["C1"], 1))
        with index._connect() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertIsNone(
                conn.execute("SELECT name FROM sqlite_master WHERE name = 'vector_lsh'").fetchone()
            )
        index.activate(older.build_id)
        self.assertEqual(index.search("Older", self.provider)[0].concept.code, "C3")


class VectorIntegrityTest(IndexTestCase):
    def test_unstorable_embeddings_cannot_replace_the_active_build(self):
        index = self.build()
        active = index.get_active_manifest()
        for value in (float("nan"), 1e39):
            with (
                self.subTest(value=value),
                patch.object(
                    self.provider,
                    "embed",
                    side_effect=lambda texts, value=value: [[value] * 128 for _ in texts],
                ),
                self.assertRaises(IndexCompatibilityError),
            ):
                index.upsert_concepts([concept("C99", "New")], None, self.provider)
            self.assertEqual(index.get_active_manifest(), active)
            self.assertIsNone(index.get_concept("C99"))

    def test_search_includes_all_concepts_beyond_the_former_scan_cutoff(self):
        provider = HashingEmbeddingProvider(dimensions=2)
        with patch.object(provider, "embed", side_effect=lambda texts: [[0.0, 1.0] for _ in texts]):
            index = LocalIndex(self.path)
            index.upsert_concepts(
                [concept(f"C{i:05}", "Other") for i in range(20001)],
                None,
                provider,
            )
        with patch.object(provider, "embed", return_value=[[1.0, 0.0]]):
            hits, total, _ = index.search_page("query", provider, mode="vector", offset=20000)
        self.assertEqual(total, 20001)
        self.assertEqual([hit.concept.code for hit in hits], ["C20000"])

    def test_malformed_or_nonfinite_vector_blobs_fail_without_a_ranking(self):
        index = self.build()
        with index._connect() as conn:
            row = conn.execute("SELECT code, vector FROM concept_vectors LIMIT 1").fetchone()
        for blob in (row[1][:-1], struct.pack("<f", float("nan")) + row[1][4:]):
            with self.subTest(size=len(blob)):
                with index._connect() as conn:
                    conn.execute(
                        "UPDATE concept_vectors SET vector = ? WHERE code = ?", (blob, row[0])
                    )
                with self.assertRaises(IndexStorageError):
                    index.search("kinase", self.provider)

    def test_nonfinite_query_embedding_is_rejected(self):
        index = self.build()
        with (
            patch.object(self.provider, "embed", return_value=[[float("inf")] * 128]),
            self.assertRaises(IndexCompatibilityError),
        ):
            index.search("kinase", self.provider)

    def test_float64_provider_rounds_consistently_before_ranking(self):
        provider = HashingEmbeddingProvider(dimensions=2)
        values = {"Alpha": [1.0, 2.0 + 1e-8], "Beta": [1.0, 2.0], "query": [1.0, 0.0]}
        with patch.object(
            provider, "embed", side_effect=lambda texts: [values[text] for text in texts]
        ):
            index = LocalIndex(self.path)
            index.upsert_concepts([concept("C1", "Alpha"), concept("C2", "Beta")], None, provider)
            hits = index.search("query", provider, mode="vector")
        self.assertEqual([hit.concept.code for hit in hits], ["C1", "C2"])
        self.assertEqual(hits[0].score, hits[1].score)
