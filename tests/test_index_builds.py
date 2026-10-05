"""Snapshot lifecycle and field attribution are caller-visible index guarantees."""

import sqlite3
from unittest.mock import patch

from nci_si_mcp.embeddings import HashingEmbeddingProvider
from nci_si_mcp.errors import (
    IndexBuildError,
    IndexCompatibilityError,
    IndexStorageError,
    correlated,
)
from nci_si_mcp.index import LocalIndex
from test_index import RAW_CONCEPTS, IndexTestCase


class BuildLifecycleTest(IndexTestCase):
    def test_unusable_build_lease_reports_storage_error_and_keeps_active_index(self):
        index = self.build()
        active = index.get_active_manifest()
        with patch("nci_si_mcp.index.uuid4") as identifier:
            identifier.return_value.hex = "f" * 32
            lease_path = self.path / f"build-{'f' * 32}.sqlite3"
            lease_path.mkdir()
            with self.assertRaises(IndexStorageError) as raised:
                index.build(RAW_CONCEPTS, None, self.provider)
        self.assertIn(str(lease_path), str(raised.exception))
        self.assertEqual(index.get_active_manifest(), active)

    def test_another_cache_writer_succeeds_mid_build_and_cannot_activate_partial_data(self):
        index = self.build()
        embed = self.provider.embed
        calls = []
        second_batch = 2

        def write_during_second_batch(texts):
            calls.append(len(texts))
            if len(calls) == second_batch:
                with index._connect() as conn:
                    building = conn.execute(
                        "SELECT build_id FROM manifests WHERE state = 'building'"
                    ).fetchone()[0]
                    count = conn.execute(
                        "SELECT COUNT(*) FROM concepts WHERE build_id = ?", (building,)
                    ).fetchone()[0]
                self.assertEqual(count, 64)
                with self.assertRaises(IndexBuildError):
                    index.activate(building)
                index.upsert_concepts(
                    [dict(RAW_CONCEPTS[0], code="C999")], None, HashingEmbeddingProvider()
                )
            return embed(texts)

        rows = [dict(RAW_CONCEPTS[0], code=f"C{number}") for number in range(65)]
        with patch.object(self.provider, "embed", side_effect=write_during_second_batch):
            built = index.build(rows, None, self.provider)
        self.assertEqual(built.concept_count, 65)
        self.assertIsNotNone(index.get_concept("C999"))
        self.assertFalse(built.active)
        self.assertEqual(list(self.path.glob("build-*.sqlite3")), [])

    def test_cache_payload_and_manifest_share_snapshot_during_activation(self):
        index = self.build()
        active = index.get_active_manifest()
        other = index.build([dict(RAW_CONCEPTS[1], version="other")], None, self.provider)
        read = LocalIndex._active_manifest

        def activate_after_manifest(conn):
            manifest = read(conn)
            index.activate(other.build_id)
            return manifest

        with patch.object(LocalIndex, "_active_manifest", staticmethod(activate_after_manifest)):
            manifest, cached = index.get_concept_snapshot("C3262")
        self.assertEqual(manifest, active)
        self.assertEqual(cached.release_version, active.release_version)
        self.assertEqual(index.get_active_manifest().release_version, "other")

    def test_activation_is_idempotent_and_unknown_rebuild_is_rejected(self):
        index = self.build()
        active = index.get_active_manifest()
        self.assertEqual(index.activate(active.build_id), active)
        with self.assertRaises(IndexBuildError):
            index.rebuild("unknown", self.provider)
        self.assertEqual(index.get_active_manifest(), active)

    def test_failed_activation_rolls_back_the_active_flag_and_retention(self):
        index = self.build()
        active = index.get_active_manifest()
        built = index.build([RAW_CONCEPTS[0]], None, self.provider)
        with index._connect() as conn:
            conn.execute(
                "CREATE TRIGGER refuse_activation BEFORE UPDATE ON manifests "
                "WHEN NEW.active = 1 BEGIN SELECT RAISE(ABORT, 'blocked'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            index.activate(built.build_id)
        self.assertEqual(index.get_active_manifest(), active)
        self.assertEqual(len(index.list_builds()), 2)

    def test_interrupted_build_stays_hidden_until_next_start_removes_it(self):
        index = self.build()
        active = index.get_active_manifest()
        embed = self.provider.embed
        calls = []
        second_batch = 2

        def interrupt_second_batch(texts):
            calls.append(len(texts))
            if len(calls) == second_batch:
                raise KeyboardInterrupt
            return embed(texts)

        rows = [dict(RAW_CONCEPTS[0], code=f"C{number}") for number in range(65)]
        with (
            patch.object(self.provider, "embed", side_effect=interrupt_second_batch),
            self.assertRaises(KeyboardInterrupt),
        ):
            index.build(rows, None, self.provider)
        self.assertEqual(index.list_builds(), [active])
        self.assertEqual(self.provider.embed, embed)
        with index._connect() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM manifests WHERE state = 'building'").fetchone()[
                    0
                ],
                1,
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0], 66)
        next_build = index.build(RAW_CONCEPTS, None, self.provider)
        self.assertEqual(next_build.concept_count, 2)
        with index._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0], 4)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM manifests WHERE state = 'building'").fetchone()[
                    0
                ],
                0,
            )

    def test_invalid_streams_never_leave_partial_snapshots(self):
        index = self.build()
        active = index.get_active_manifest()
        cases = [
            [RAW_CONCEPTS[0], dict(RAW_CONCEPTS[1], version="other")],
            [dict(RAW_CONCEPTS[0], code="")],
            [RAW_CONCEPTS[0], RAW_CONCEPTS[0]],
            [dict(RAW_CONCEPTS[0], name="", definitions=[], synonyms=[])],
        ]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(IndexBuildError):
                index.build(iter(rows), None, self.provider)
        with self.assertRaises(IndexCompatibilityError):
            index.build(RAW_CONCEPTS, None, self.provider, expected_release_version="other")
        self.assertEqual(index.list_builds(), [active])

    def test_embedding_dimension_change_between_batches_rolls_back(self):
        index = self.build()
        embed = self.provider.embed
        calls = []

        def changing(texts):
            calls.append(len(texts))
            vectors = embed(texts)
            return vectors if len(calls) == 1 else [row[:2] for row in vectors]

        rows = [dict(RAW_CONCEPTS[0], code=f"C{number}") for number in range(65)]
        with (
            patch.object(self.provider, "embed", side_effect=changing),
            self.assertRaisesRegex(IndexCompatibilityError, "changed during"),
        ):
            index.build(rows, None, self.provider)
        self.assertEqual(index.get_active_manifest().concept_count, 2)
        self.assertEqual(len(index.list_builds()), 1)

    def test_inactive_build_cannot_change_active_bm25_statistics(self):
        index = self.build()
        before = index.search("kinase tumor", self.provider, mode="bm25")
        index.build([dict(RAW_CONCEPTS[0], code=f"C{n}") for n in range(50)], None, self.provider)
        self.assertEqual(index.search("kinase tumor", self.provider, mode="bm25"), before)

    def test_inactive_same_release_build_does_not_change_readers_and_can_roll_back(self):
        index = self.build()
        old = index.get_active_manifest()
        changed = dict(RAW_CONCEPTS[1], name="Changed Name")

        built = index.build([changed], None, self.provider)

        self.assertFalse(built.active)
        self.assertEqual(index.get_active_manifest(), old)
        self.assertEqual(index.get_concept("C3262").preferred_name, "Neoplasm")
        index.activate(built.build_id)
        self.assertEqual(index.get_concept("C3262").preferred_name, "Changed Name")
        index.activate(old.build_id)
        self.assertEqual(index.get_concept("C3262").preferred_name, "Neoplasm")

    def test_third_activation_keeps_only_active_and_previous_build(self):
        index = self.build()
        first = index.get_active_manifest()
        second = index.build([RAW_CONCEPTS[0]], None, self.provider)
        index.activate(second.build_id)
        third = index.build([RAW_CONCEPTS[1]], None, self.provider)
        index.activate(third.build_id)

        self.assertEqual(
            {item.build_id for item in index.list_builds()}, {second.build_id, third.build_id}
        )
        with self.assertRaises(IndexBuildError):
            index.activate(first.build_id)
        index.activate(second.build_id)
        self.assertEqual(index.get_concept("C40704").preferred_name, RAW_CONCEPTS[0]["name"])

    def test_invalid_build_keeps_active_snapshot(self):
        index = self.build()
        active = index.get_active_manifest()

        with self.assertRaises(IndexBuildError):
            index.build([], None, self.provider)

        self.assertEqual(index.get_active_manifest(), active)
        self.assertEqual(index.get_concept("C3262").preferred_name, "Neoplasm")


class FieldSearchTest(IndexTestCase):
    def test_name_wins_field_ties_and_exact_name_beats_an_adversarial_model(self):
        index = LocalIndex(self.path)
        with patch.object(
            self.provider, "embed", side_effect=lambda texts: [[1.0, 0.0] for _ in texts]
        ):
            index.upsert_concepts(RAW_CONCEPTS, None, self.provider)
            tied = index.search("arbitrary query", self.provider, mode="vector")
            self.assertEqual([hit.matched_on for hit in tied], ["name", "name"])
        with patch.object(self.provider, "embed", return_value=[[0.0, 1.0]]):
            exact = index.search(
                "Receptor Tyrosine Kinase Inhibition", self.provider, limit=1, mode="vector"
            )
        self.assertEqual(exact[0].concept.code, "C40704")
        self.assertEqual((exact[0].matched_on, exact[0].score), ("name", 1.0))

    def test_public_manifest_excludes_operator_state(self):
        index = self.build()
        with correlated():
            public = index.get_active_manifest().to_result()
        self.assertEqual(
            set(public),
            {"terminology", "version", "concepts", "embedding", "builtAt", "provenance"},
        )
        self.assertEqual(
            public["embedding"], {"provider": "hashing", "model": "hashing-128", "dimensions": 128}
        )

    def test_identical_text_is_embedded_once_with_name_priority(self):
        calls = []
        embed = self.provider.embed

        def record(texts):
            calls.extend(texts)
            return embed(texts)

        self.provider.embed = record
        raw = dict(
            RAW_CONCEPTS[1],
            synonyms=[{"name": "Neoplasm"}, {"name": "Tumor"}],
            definitions=[{"definition": "Neoplasm"}],
        )
        index = LocalIndex(self.path)
        index.upsert_concepts([raw], None, self.provider)

        self.assertEqual(calls, ["Neoplasm", "Tumor"])
        self.assertEqual(index.search("Neoplasm", self.provider)[0].matched_on, "name")

    def test_exact_name_normalizes_case_nfc_and_whitespace(self):
        raw = dict(RAW_CONCEPTS[1], name="Café   Cell", synonyms=[], definitions=[])
        index = self.build([raw, RAW_CONCEPTS[0]])

        for mode in ("vector", "hybrid"):
            with self.subTest(mode=mode):
                hits = index.search("  CAFE\u0301\tCELL ", self.provider, limit=1, mode=mode)
                self.assertEqual(hits[0].concept.code, "C3262")
                self.assertEqual(hits[0].matched_on, "name")

    def test_synonym_and_definition_hits_name_the_matching_field(self):
        index = self.build()

        for query, expected in (("Tumor", "synonym"), ("benign malignant tissue", "definition")):
            with self.subTest(query=query):
                hit = index.search(query, self.provider, mode="bm25")[0]
                self.assertEqual(hit.concept.code, "C3262")
                self.assertEqual(hit.matched_on, expected)
