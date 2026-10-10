"""Storage and operator-state regressions from the independent mutation review."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from unittest.mock import patch

import test_index
from fakes import concept, release
from nci_si_mcp import index as index_module
from nci_si_mcp.errors import IndexBuildError, IndexCompatibilityError, PlatformError
from nci_si_mcp.evs import EVSClient, EVSResponseError, normalize_concept
from nci_si_mcp.index import LocalIndex, evaluation_identity
from nci_si_mcp.index_storage import clean_stale_builds, concept_fields, fts_table, name_key
from nci_si_mcp.indexing import full_build
from test_index import IndexTestCase
from test_server import ServerFixture


def update_manifest(index, manifest):
    with index._connect() as conn:
        conn.execute(
            "UPDATE manifests SET payload = ? WHERE build_id = ?",
            (json.dumps(manifest.to_dict()), manifest.build_id),
        )


class StorageReviewTest(IndexTestCase):
    def test_classified_rebuild_does_not_claim_an_unclassified_source(self):
        index = LocalIndex(self.path)
        for kind in ("sample", "production"):
            with self.subTest(kind=kind):
                original = index.build([concept("C1")], None, self.provider, build_kind=kind)
                rebuilt = index.rebuild(original.build_id, self.provider)
                self.assertEqual(rebuilt.build_kind, kind)
                self.assertIsNone(rebuilt.unclassified_source_build)

    def test_sample_needing_rebuild_cannot_be_updated(self):
        index = self.build([concept("C1")])
        active = replace(index.get_active_manifest(), needs_rebuild=True)
        update_manifest(index, active)
        with self.assertRaisesRegex(PlatformError, "index-rebuild"):
            index.upsert_concepts([concept("C2")], None, self.provider)
        self.assertEqual(index.get_active_manifest(), active)
        self.assertIsNone(index.get_concept_snapshot("C2")[1])

    def test_expired_cursor_names_requested_and_current_releases(self):
        index = self.build([concept("C1")])
        old = index.get_active_manifest()
        index.upsert_concepts([concept("C2", version="new")], None, self.provider)
        with self.assertRaises(PlatformError) as raised:
            index.search_page("term", self.provider, build_id=old.build_id, requested_release="old")
        self.assertEqual(raised.exception.code, "cursor_expired")
        self.assertEqual(
            raised.exception.details, {"cursorRelease": "old", "currentRelease": "new"}
        )

    def test_page_ranks_continue_after_offset(self):
        index = self.build([concept(f"C{i}", "Same") for i in range(1, 5)])
        hits, total, _ = index.search_page("Same", self.provider, mode="bm25", offset=2, limit=2)
        self.assertEqual(([hit.rank for hit in hits], total), ([3, 4], 4))

    def test_missing_dimensions_are_derived_for_sample_compatibility(self):
        index = self.build([concept("C1", "Primary name", synonyms=[{"name": "Distinct alias"}])])
        active = replace(index.get_active_manifest(), embedding_dimensions=None)
        update_manifest(index, active)
        updated = index.upsert_concepts([concept("C2")], None, self.provider)
        self.assertEqual(updated.embedding_dimensions, self.provider.dimensions)
        self.assertIsNotNone(index.get_concept_snapshot("C2")[1])

    def test_declared_dimensions_are_not_replaced_by_stored_vector_width(self):
        index = self.build([concept("C1")])
        active = replace(index.get_active_manifest(), embedding_dimensions=7)
        update_manifest(index, active)
        with self.assertRaises(IndexCompatibilityError):
            index.upsert_concepts([concept("C2")], None, self.provider)
        self.assertEqual(index.get_active_manifest(), active)

    def test_evaluation_inputs_survive_concurrent_candidate_pruning(self):
        index = self.build([concept("C1")])
        candidate = index.build([concept("C2")], None, self.provider)
        replacement = index.build([concept("C3")], None, self.provider)
        original = index_module._completed_manifest

        def prune_after_read(conn, build):
            manifest = original(conn, build)
            index.activate(replacement.build_id)
            return manifest

        with patch.object(index_module, "_completed_manifest", side_effect=prune_after_read):
            manifest, missing = index.evaluation_inputs(candidate.build_id, {"C2"})
        self.assertEqual((manifest.build_id, missing), (candidate.build_id, []))
        self.assertNotIn(candidate.build_id, {item.build_id for item in index.list_builds()})

    def test_evaluation_record_holds_write_lock_before_reading_identity(self):
        index = self.build([concept("C1")])
        candidate = index.get_active_manifest()
        original = index_module._completed_manifest
        blocked = []

        def competing_write(conn, build):
            manifest = original(conn, build)
            with closing(sqlite3.connect(index.db_path, timeout=0)) as other:
                try:
                    other.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError as exc:
                    blocked.append(exc.sqlite_errorcode)
            return manifest

        report = evaluation_identity(candidate) | {
            "evaluation_version": "review",
            "evaluation_score": 1,
        }
        with patch.object(index_module, "_completed_manifest", side_effect=competing_write):
            result = index.record_evaluation(candidate.build_id, report)
        self.assertEqual(blocked, [sqlite3.SQLITE_BUSY])
        self.assertEqual(result.evaluation_report, report)

    def test_pruning_removes_vectors_and_fts_storage(self):
        index = self.build([concept("C1")])
        old = index.get_active_manifest().build_id
        index.upsert_concepts([concept("C2")], None, self.provider)
        index.upsert_concepts([concept("C3")], None, self.provider)
        with index._connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM concept_vectors WHERE build_id = ?", (old,)
                ).fetchone()[0],
                0,
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE name = ?", (fts_table(old),)
                ).fetchone()
            )

    def test_duplicate_field_text_keeps_name_before_synonym_before_definition(self):
        raw = concept(
            "C1",
            "Name",
            synonyms=[{"name": "Name"}, {"name": "Alias"}],
            definitions=[{"definition": "Name"}, {"definition": "Alias"}],
        )
        item = normalize_concept(raw, None, "active_cache")
        self.assertEqual(concept_fields(item), [("name", "Name"), ("synonym", "Alias")])

    def test_exact_name_key_collapses_whitespace_and_normalizes_unicode(self):
        self.assertEqual(name_key("  CAFE\u0301\t  cell\n"), "café cell")

    def test_nonbusy_failure_cleaning_a_stale_build_is_not_swallowed(self):
        index = self.build([concept("C1")])
        with index._connect() as conn:
            conn.execute("UPDATE manifests SET active = 0, state = 'building'")
        error = sqlite3.OperationalError("lease I/O failure")
        error.sqlite_errorcode = sqlite3.SQLITE_IOERR
        with (
            index._connect() as conn,
            patch("nci_si_mcp.index_storage.delete_build", side_effect=error),
            self.assertRaisesRegex(sqlite3.OperationalError, "lease I/O failure"),
        ):
            clean_stale_builds(conn, self.path)

    def test_build_identifier_cannot_form_an_arbitrary_sql_identifier(self):
        for value in ("x" * 32, "a" * 31, "a" * 32 + "; DROP TABLE concepts"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "identifier"):
                fts_table(value)

    def test_building_manifest_cannot_be_active(self):
        index = self.build([concept("C1")])
        with index._connect() as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE manifests SET state = 'building' WHERE active = 1")
        self.assertIsNotNone(index.get_active_manifest())

    def test_only_one_manifest_can_be_active(self):
        index = self.build([concept("C1")])
        candidate = index.build([concept("C2")], None, self.provider)
        with index._connect() as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE manifests SET active = 1 WHERE build_id = ?", (candidate.build_id,)
            )
        self.assertEqual(sum(item.active for item in index.list_builds()), 1)


class LegacyStorageReviewTest(IndexTestCase):
    legacy_database = test_index.MigrationTest.legacy_database

    def test_empty_legacy_manifest_without_dimensions_still_blocks_sample_updates(self):
        self.legacy_database([("26.06e", concept("C1"))])
        with closing(sqlite3.connect(self.path / "nci_si.sqlite3")) as conn, conn:
            conn.execute("DELETE FROM concepts")
            conn.execute("UPDATE manifests SET payload = json_set(payload, '$.concept_count', 0)")
        index = LocalIndex(self.path)
        active = index.get_active_manifest()
        self.assertIsNone(active.embedding_dimensions)
        with self.assertRaises(IndexBuildError) as raised:
            index.upsert_concepts([concept("C2")], None, self.provider)
        self.assertEqual(index.list_builds(), [active])
        self.assertIsNone(index.get_concept_snapshot("C2")[1])
        self.assertIn("separate data directory", str(raised.exception))

    def test_legacy_migration_derives_dimensions_and_retains_status(self):
        self.legacy_database([("26.06e", concept("C1", conceptStatus="Retired_Concept"))])
        index = LocalIndex(self.path)
        self.assertEqual(index.get_active_manifest().embedding_dimensions, self.provider.dimensions)
        with index._connect() as conn:
            self.assertEqual(
                conn.execute("SELECT status FROM concepts").fetchone()[0], "Retired_Concept"
            )

    def test_orphan_legacy_concept_aborts_migration_without_losing_it(self):
        self.legacy_database([("26.06e", concept("C1"))])
        with closing(sqlite3.connect(self.path / "nci_si.sqlite3")) as conn, conn:
            conn.execute("UPDATE concepts SET release_version = 'orphan'")
        with self.assertRaisesRegex(IndexCompatibilityError, "without a manifest"):
            LocalIndex(self.path)
        with closing(sqlite3.connect(self.path / "nci_si.sqlite3")) as conn:
            self.assertEqual(
                conn.execute("SELECT release_version FROM concepts").fetchone()[0], "orphan"
            )


class FullBuildStorageReviewTest(ServerFixture):
    def setUp(self):
        super().setUp()
        self.context.evs = EVSClient("https://example.invalid")

    def test_downloaded_build_must_match_the_requested_release(self):
        with (
            patch.object(
                self.context.evs,
                "get_index_page",
                return_value=(1, [concept("C1", version="wrong")]),
            ),
            self.assertRaises(IndexCompatibilityError),
        ):
            full_build(self.context, release())
        self.assertEqual(self.context.index.list_builds(), [])

    def test_full_build_rejects_null_nontext_and_whitespace_names(self):
        for name in (None, 7, " \t\n "):
            with (
                self.subTest(name=name),
                patch.object(
                    self.context.evs,
                    "get_index_page",
                    return_value=(
                        1,
                        [concept("C1", synonyms=[{"name": "Searchable alias"}]) | {"name": name}],
                    ),
                ),
                self.assertRaisesRegex(EVSResponseError, "preferred name"),
            ):
                full_build(self.context, release())
        self.assertEqual(self.context.index.list_builds(), [])
