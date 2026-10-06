"""SQLite-backed concept cache and local retrieval index.

Builds are immutable snapshots. Activation retains the previous snapshot for rollback.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from itertools import batched, chain, groupby
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any, Literal
from uuid import uuid4

from .audit import emit
from .embeddings import EmbeddingProvider
from .errors import (
    IndexBuildError,
    IndexCompatibilityError,
    IndexEvaluationError,
    IndexStateError,
    IndexStorageError,
    NoActiveIndexError,
    PlatformError,
)
from .evs import normalize_concept
from .index_scoring import rank_page
from .index_storage import (
    FIELD_KINDS,
    SCHEMA_VERSION,
    build_lease,
    clean_stale_builds,
    concept_fields,
    create_fts,
    delete_build,
    fts_table,
    migrate,
    name_key,
    vector_bytes,
)
from .models import IndexManifest, NcitConcept, SearchHit, Truncation, utc_now_iso
from .validation import MAX_INDEX_SEARCH_LIMIT, validate_search

logger = logging.getLogger(__name__)


def require_index_release(manifest: IndexManifest, requested: str | None) -> None:
    if requested is not None and manifest.release_version != requested:
        raise PlatformError(
            "release_mismatch",
            "The index holds another release. Rebuild it for the requested release.",
            requested=requested,
            served=[manifest.release_version],
            source="index",
        )


# SQL built with f-strings below interpolates only table names and predicates
# written in this module, or lists of "?" placeholders; every value is bound.

_SQL_CHUNK = 500


def _distinct_concepts(
    raw_concepts: Iterable[dict[str, object]], release_date: str | None
) -> list[NcitConcept]:
    """Normalize the payloads, keeping one concept per code."""

    by_code = {
        str(raw.get("code") or ""): normalize_concept(
            raw, release_date=release_date, source="active_cache"
        )
        for raw in raw_concepts
    }
    concepts = list(by_code.values())
    if not concepts:
        raise IndexBuildError("No concepts were provided for indexing")
    if "" in by_code:
        raise IndexBuildError("Indexed concepts must include a code")
    return concepts


def _single_release(concepts: list[NcitConcept]) -> str:
    release_version = concepts[0].release_version
    if not release_version:
        raise IndexBuildError("Indexed concepts must include a release version")
    if any(concept.release_version != release_version for concept in concepts):
        raise IndexBuildError("Cannot mix concept release versions in one index build")
    return release_version


def _embed(embedding_provider: EmbeddingProvider, texts: list[str]) -> list[list[float]]:
    vectors = embedding_provider.embed(texts)
    if len(vectors) != len(texts):
        raise IndexCompatibilityError("Embedding provider returned an unexpected vector count")
    dimensions = len(vectors[0])
    if dimensions < 1 or any(len(vector) != dimensions for vector in vectors):
        raise IndexCompatibilityError("Embedding provider returned inconsistent vector dimensions")
    return vectors


def _results_truncation(scored: int, limit: int) -> Truncation:
    """The truncation record of a search whose `limit` may have left scored concepts out."""

    if scored <= limit:
        return Truncation(occurred=False)
    return Truncation(
        occurred=True,
        bound="results",
        limit=limit,
        reached=limit,
        omitted=scored - limit,
        exact=True,
    )


def _query_vector(
    embedding_provider: EmbeddingProvider, manifest: IndexManifest, query: str
) -> list[float]:
    query_vectors = embedding_provider.embed([query])
    if len(query_vectors) != 1:
        raise IndexCompatibilityError(
            "Embedding provider returned an unexpected query vector count"
        )
    query_vector = query_vectors[0]
    if not manifest.embedding_matches(
        embedding_provider.name, embedding_provider.model, len(query_vector)
    ):
        raise IndexCompatibilityError("Query embedding dimensions do not match the active index")
    return query_vector


def _store_fields(
    conn: sqlite3.Connection,
    build_id: str,
    fields: list[tuple[str, str, str]],
    vectors: list[list[float]],
) -> None:
    paired = zip(fields, vectors, strict=True)
    for code, items in groupby(paired, lambda item: item[0][0]):
        kinds, blobs = [], []
        for position, ((_, kind, text), vector) in enumerate(items):
            cursor = conn.execute(
                "INSERT INTO fields(build_id, code, kind, text, position) VALUES (?, ?, ?, ?, ?)",
                (build_id, code, kind, text, position),
            )
            conn.execute(
                f"INSERT INTO {fts_table(build_id)}(rowid, code, kind, text) VALUES (?, ?, ?, ?)",  # noqa: S608
                (cursor.lastrowid, code, kind, text),
            )
            kinds.append(FIELD_KINDS.index(kind))
            blobs.append(vector_bytes(vector))
        conn.execute(
            "INSERT INTO concept_vectors VALUES (?, ?, ?, ?)",
            (build_id, code, bytes(kinds), b"".join(blobs)),
        )


def _prepare_fields(
    concepts: tuple[NcitConcept, ...],
    release: str,
    seen: set[str],
) -> list[tuple[str, str, str]]:
    fields = []
    for concept in concepts:
        if concept.release_version != release:
            raise IndexBuildError("Cannot mix concept release versions in one index build")
        if not concept.code or concept.code in seen:
            raise IndexBuildError("Index build contains a missing or duplicate concept code")
        seen.add(concept.code)
        text_fields = concept_fields(concept)
        if not text_fields:
            raise IndexBuildError(f"Concept {concept.code} has no indexable text")
        fields.extend((concept.code, kind, text) for kind, text in text_fields)
    return fields


def _write_batch(
    conn: sqlite3.Connection,
    build_id: str,
    concepts: tuple[NcitConcept, ...],
    fields: list[tuple[str, str, str]],
    vectors: list[list[float]],
) -> None:
    conn.executemany(
        "INSERT INTO concepts VALUES (?, ?, ?, ?, ?)",
        [
            (
                build_id,
                item.code,
                json.dumps(item.to_stored()),
                name_key(item.preferred_name),
                item.raw.get("conceptStatus"),
            )
            for item in concepts
        ],
    )
    _store_fields(conn, build_id, fields, vectors)


def _check_dimensions(previous: int, width: int, compatible: IndexManifest | None) -> None:
    if previous and width != previous:
        raise IndexCompatibilityError("Embedding dimensions changed during the build")
    if compatible and compatible.embedding_dimensions not in (None, width):
        raise IndexCompatibilityError(
            f"Embedding mismatch; rebuild {compatible.index_path} with index-rebuild"
        )


def _check_sample_snapshot(conn: sqlite3.Connection, active: IndexManifest | None) -> None:
    row = conn.execute("SELECT build_id FROM manifests WHERE active = 1").fetchone()
    expected = active.build_id if active else None
    current = row[0] if row else None
    if expected != current:
        raise IndexStateError("The active index changed during sample indexing; retry the sample")


class LocalIndex:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "nci_si.sqlite3"
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Commit or roll back and close; database failures retain their file context."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.row_factory = sqlite3.Row
                with conn:
                    yield conn
            finally:
                conn.close()
        except sqlite3.Error as exc:
            if isinstance(exc, sqlite3.OperationalError) or type(exc) is sqlite3.DatabaseError:
                raise IndexStorageError(f"{exc} ({self.db_path})") from exc
            raise

    def _init_db(self) -> None:
        with self._connect() as conn:
            if self._schema_current(conn):
                return
            if conn.execute("PRAGMA page_count").fetchone()[0] == 0:
                # Larger pages avoid overflow-page reads for per-concept vector BLOBs.
                conn.execute("PRAGMA page_size = 65536")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("BEGIN IMMEDIATE")
            # Another opener may have migrated while this one waited for the lock.
            if not self._schema_current(conn):
                migrate(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _schema_current(self, conn: sqlite3.Connection) -> bool:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise IndexCompatibilityError(
                f"Index schema {version} at {self.db_path} is newer than supported {SCHEMA_VERSION}"
            )
        return version == SCHEMA_VERSION

    @staticmethod
    def _manifest(row: sqlite3.Row) -> IndexManifest:
        return replace(
            IndexManifest.from_payload(json.loads(row["payload"])), active=bool(row["active"])
        )

    @staticmethod
    def _active_manifest(conn: sqlite3.Connection) -> IndexManifest | None:
        row = conn.execute(
            "SELECT payload, active FROM manifests WHERE active = 1 AND state = 'complete'"
        ).fetchone()
        return LocalIndex._manifest(row) if row else None

    def get_active_manifest(self) -> IndexManifest | None:
        with self._connect() as conn:
            return self._active_manifest(conn)

    def verify_active(self, provider: EmbeddingProvider) -> IndexManifest:
        """Open and validate the active search build without embedding or upstream I/O."""
        with self._connect() as conn:
            return self._searchable_manifest(conn, provider, None, None)

    def list_builds(self) -> list[IndexManifest]:
        """Completed snapshots, including the rollback target and pending activation."""
        with self._connect() as conn:
            return [
                self._manifest(row)
                for row in conn.execute(
                    "SELECT payload, active FROM manifests WHERE state = 'complete'"
                )
            ]

    def evaluation_inputs(
        self, build_id: str, expected_codes: set[str]
    ) -> tuple[IndexManifest, list[str]]:
        """Read the candidate identity and missing judgments from one database snapshot."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            manifest = _completed_manifest(conn, build_id)
            present = _concept_payloads(conn, build_id, sorted(expected_codes))
            return manifest, sorted(expected_codes - present.keys())

    def record_evaluation(self, build_id: str, report: dict[str, Any]) -> IndexManifest:
        """Persist an evaluation only while the same immutable candidate still exists."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            manifest = _completed_manifest(conn, build_id)
            identity = evaluation_identity(manifest)
            if any(report.get(key) != value for key, value in identity.items()):
                raise IndexBuildError("Evaluation identity does not match the candidate build")
            updated = replace(
                manifest,
                evaluation_version=report["evaluation_version"],
                evaluation_score=report["evaluation_score"],
                evaluation_report=report,
            )
            conn.execute(
                "UPDATE manifests SET payload = ? WHERE build_id = ?",
                (json.dumps(updated.to_dict()), build_id),
            )
            return updated

    def build(
        self,
        raw_concepts: Iterable[dict[str, object]],
        release_date: str | None,
        embedding_provider: EmbeddingProvider,
        expected_release_version: str | None = None,
        *,
        build_kind: Literal["sample", "production"] = "sample",
    ) -> IndexManifest:
        """Embed outside transactions; interrupted builds stay hidden until cleaned."""
        concepts = (normalize_concept(raw, release_date, "active_cache") for raw in raw_concepts)
        first = next(concepts, None)
        if first is None:
            raise IndexBuildError("No concepts were provided for indexing")
        release = _single_release([first])
        if expected_release_version is not None and release != expected_release_version:
            raise IndexCompatibilityError(
                "Concept payload release did not match the selected release"
            )
        return self._build_stream(
            chain([first], concepts),
            release_date,
            embedding_provider,
            release,
            build_kind=build_kind,
        )

    def _build_stream(
        self,
        concepts: Iterable[NcitConcept],
        release_date: str | None,
        provider: EmbeddingProvider,
        release: str,
        compatible: IndexManifest | None = None,
        *,
        build_kind: Literal["legacy", "sample", "production"] = "sample",
        unclassified_source_build: str | None = None,
    ) -> IndexManifest:
        build_id = uuid4().hex
        manifest = IndexManifest(
            terminology="ncit",
            release_version=release,
            release_date=release_date,
            embedding_provider=provider.name,
            embedding_model=provider.model,
            concept_count=0,
            built_at=utc_now_iso(),
            index_path=str(self.db_path),
            build_id=build_id,
            build_kind=build_kind,
            unclassified_source_build=unclassified_source_build,
        )
        with build_lease(self.data_dir, build_id):
            self._start_build(manifest)
            count, dimensions = self._write_batches(manifest, concepts, provider, compatible)
            completed = replace(manifest, concept_count=count, embedding_dimensions=dimensions)
            with self._connect() as conn:
                conn.execute(
                    "UPDATE manifests SET state = 'complete', payload = ? WHERE build_id = ?",
                    (json.dumps(completed.to_dict()), build_id),
                )
            return completed

    def _start_build(self, manifest: IndexManifest) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            clean_stale_builds(conn, self.data_dir)
            create_fts(conn, manifest.build_id)
            conn.execute(
                "INSERT INTO manifests VALUES (?, ?, 0, 'building')",
                (manifest.build_id, json.dumps(manifest.to_dict())),
            )

    def _write_batches(
        self,
        manifest: IndexManifest,
        concepts: Iterable[NcitConcept],
        provider: EmbeddingProvider,
        compatible: IndexManifest | None,
    ) -> tuple[int, int]:
        seen: set[str] = set()
        dimensions = 0
        for number, batch in enumerate(batched(concepts, 64, strict=False), 1):
            fields = _prepare_fields(batch, manifest.release_version, seen)
            vectors = _embed(provider, [text for _, _, text in fields])
            width = len(vectors[0])
            _check_dimensions(dimensions, width, compatible)
            with self._connect() as conn:
                _write_batch(conn, manifest.build_id, batch, fields, vectors)
            dimensions = width
            if number % 16 == 0:
                emit(
                    logger,
                    logging.INFO,
                    "index_embedding_progress",
                    build_id=manifest.build_id,
                    concepts=len(seen),
                    batches=number,
                )
        return len(seen), dimensions

    def activate(self, build_id: str) -> IndexManifest:
        """Atomically activate a completed snapshot and retain only its predecessor."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._activate(conn, build_id)

    @staticmethod
    def _activate(conn: sqlite3.Connection, build_id: str) -> IndexManifest:
        row = conn.execute(
            "SELECT payload, active FROM manifests WHERE build_id = ? AND state = 'complete'",
            (build_id,),
        ).fetchone()
        if not row:
            raise IndexStateError(
                "Unknown completed build; list available builds with index-builds"
            )
        manifest = LocalIndex._manifest(row)
        _require_evaluated(manifest)
        if manifest.active:
            return manifest
        previous = conn.execute("SELECT build_id FROM manifests WHERE active = 1").fetchone()
        keep = {build_id, previous["build_id"] if previous else build_id}
        conn.execute("UPDATE manifests SET active = 0 WHERE active = 1")
        conn.execute("UPDATE manifests SET active = 1 WHERE build_id = ?", (build_id,))
        stale = [
            row[0]
            for row in conn.execute("SELECT build_id FROM manifests WHERE state = 'complete'")
            if row[0] not in keep
        ]
        for identifier in stale:
            delete_build(conn, identifier)
        emit(
            logger,
            logging.INFO,
            "index_activated",
            build_id=build_id,
            release=manifest.release_version,
        )
        return replace(manifest, active=True)

    def rebuild(self, build_id: str, provider: EmbeddingProvider) -> IndexManifest:
        """Offline rebuild from stored payloads; explicit activation is a separate step."""
        with TemporaryFile(mode="w+t", encoding="utf-8") as spool:
            with self._connect() as conn:
                conn.execute("BEGIN")
                row = conn.execute(
                    "SELECT payload, active FROM manifests "
                    "WHERE build_id = ? AND state = 'complete'",
                    (build_id,),
                ).fetchone()
                if not row:
                    raise IndexStateError("Unknown build; list available builds with index-builds")
                old = self._manifest(row)
                for item in conn.execute(
                    "SELECT payload FROM concepts WHERE build_id = ? ORDER BY code", (build_id,)
                ):
                    spool.write(item[0] + "\n")
            spool.seek(0)
            concepts = (NcitConcept(**json.loads(line)) for line in spool)
            return self._build_stream(
                concepts,
                old.release_date,
                provider,
                old.release_version,
                build_kind="sample" if old.build_kind == "sample" else "production",
                unclassified_source_build=(
                    old.build_id if old.build_kind not in {"sample", "production"} else None
                ),
            )

    def upsert_concepts(
        self,
        raw_concepts: Iterable[dict[str, object]],
        release_date: str | None,
        embedding_provider: EmbeddingProvider,
        expected_release_version: str | None = None,
    ) -> IndexManifest:
        """Developer sample update: build the combined same-release sample, then activate."""
        concepts = _distinct_concepts(raw_concepts, release_date)
        release = _single_release(concepts)
        if expected_release_version is not None and release != expected_release_version:
            raise IndexCompatibilityError(
                "Concept payload release did not match the selected release"
            )
        with self._connect() as conn:
            conn.execute("BEGIN")
            active = _stored_dimensions(conn, self._active_manifest(conn))
            if active and active.build_kind != "sample":
                raise IndexEvaluationError(
                    f"Sample updates cannot modify {active.build_kind} build {active.build_id}; "
                    "recreate the sample with index-sample in a separate data directory"
                )
            combined = self._sample_concepts(conn, active, concepts, embedding_provider, release)
        compatible = active if active and active.release_version == release else None
        built = self._build_stream(combined, release_date, embedding_provider, release, compatible)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _check_sample_snapshot(conn, active)
            return self._activate(conn, built.build_id)

    @staticmethod
    def _sample_concepts(
        conn: sqlite3.Connection,
        active: IndexManifest | None,
        concepts: list[NcitConcept],
        provider: EmbeddingProvider,
        release: str,
    ) -> list[NcitConcept]:
        if active is None or active.release_version != release:
            return concepts
        if active.needs_rebuild:
            raise PlatformError(
                "capability_unavailable", "Run index-rebuild before updating a legacy sample."
            )
        if not active.embedding_matches(provider.name, provider.model):
            raise IndexCompatibilityError(
                f"Embedding mismatch; rebuild {active.index_path} with index-rebuild"
            )
        previous = {
            row["code"]: NcitConcept(**json.loads(row["payload"]))
            for row in conn.execute(
                "SELECT code, payload FROM concepts WHERE build_id = ?", (active.build_id,)
            )
        }
        previous.update((concept.code, concept) for concept in concepts)
        return list(previous.values())

    def get_concept(self, code: str) -> NcitConcept | None:
        return self.get_concept_snapshot(code)[1]

    def get_concept_snapshot(self, code: str) -> tuple[IndexManifest | None, NcitConcept | None]:
        """Read the cache and the release used to validate it in one snapshot."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            manifest = self._active_manifest(conn)
            row = conn.execute(
                "SELECT concepts.payload FROM concepts JOIN manifests USING (build_id) "
                "WHERE manifests.active = 1 AND concepts.code = ?",
                (code,),
            ).fetchone()
        return manifest, NcitConcept(**json.loads(row[0])) if row else None

    def search(
        self,
        query: str,
        embedding_provider: EmbeddingProvider,
        limit: int = 10,
        mode: str = "hybrid",
    ) -> list[SearchHit]:
        return self.search_with_truncation(query, embedding_provider, limit, mode)[0]

    def search_with_truncation(
        self,
        query: str,
        embedding_provider: EmbeddingProvider,
        limit: int = 10,
        mode: str = "hybrid",
        *,
        requested_release: str | None = None,
    ) -> tuple[list[SearchHit], Truncation]:
        hits, truncation, _ = self.search_snapshot(
            query, embedding_provider, limit, mode, requested_release=requested_release
        )
        return hits, truncation

    def search_snapshot(
        self,
        query: str,
        embedding_provider: EmbeddingProvider,
        limit: int = 10,
        mode: str = "hybrid",
        *,
        requested_release: str | None = None,
    ) -> tuple[list[SearchHit], Truncation, IndexManifest]:
        """Legacy CLI search reports any hits omitted by its one-page limit."""
        hits, total, manifest = self.search_page(
            query, embedding_provider, limit, mode, requested_release=requested_release
        )
        return hits, _results_truncation(total, limit), manifest

    def search_page(
        self,
        query: str,
        embedding_provider: EmbeddingProvider,
        limit: int = 10,
        mode: str = "hybrid",
        *,
        requested_release: str | None = None,
        offset: int = 0,
        build_id: str | None = None,
        retired_status: str | None = None,
    ) -> tuple[list[SearchHit], int, IndexManifest]:
        """Read the active identity, complete ranking and page in one snapshot."""
        query, limit, mode = validate_search(query, limit, mode, maximum=MAX_INDEX_SEARCH_LIMIT)
        with self._connect() as conn:
            conn.execute("PRAGMA temp_store = FILE")
            conn.execute("BEGIN")
            manifest = self._searchable_manifest(
                conn, embedding_provider, build_id, requested_release
            )
            require_index_release(manifest, requested_release)
            hits, total = _ranked_page(
                conn, manifest, embedding_provider, query, limit, mode, offset, retired_status
            )
            return hits, total, manifest

    def search_build(
        self,
        build_id: str,
        query: str,
        provider: EmbeddingProvider,
        limit: int = 10,
        mode: str = "hybrid",
    ) -> tuple[list[SearchHit], IndexManifest]:
        """Evaluate a completed candidate without changing activation or rollback state."""
        query, limit, mode = validate_search(query, limit, mode, maximum=MAX_INDEX_SEARCH_LIMIT)
        with self._connect() as conn:
            conn.execute("PRAGMA temp_store = FILE")
            conn.execute("BEGIN")
            manifest = _completed_manifest(conn, build_id)
            _require_searchable(manifest, provider)
            hits, _ = _ranked_page(conn, manifest, provider, query, limit, mode)
            return hits, manifest

    def _searchable_manifest(
        self,
        conn: sqlite3.Connection,
        provider: EmbeddingProvider,
        build_id: str | None,
        requested_release: str | None,
    ) -> IndexManifest:
        manifest = self._active_manifest(conn)
        if manifest is None:
            raise NoActiveIndexError("No active NCIt index is available")
        if build_id is not None and build_id != manifest.build_id:
            raise PlatformError(
                "cursor_expired",
                "The active index build changed. Restart the search against the active build.",
                cursorRelease=requested_release or manifest.release_version,
                currentRelease=manifest.release_version,
            )
        _require_searchable(manifest, provider)
        return manifest


def _require_searchable(manifest: IndexManifest, provider: EmbeddingProvider) -> None:
    if manifest.needs_rebuild:
        raise PlatformError(
            "capability_unavailable",
            "Run index-rebuild with this build id, then index-activate, to enable field search.",
        )
    if not manifest.embedding_matches(provider.name, provider.model):
        raise IndexCompatibilityError(
            "Index embedding provider/model does not match runtime configuration"
        )


def evaluation_identity(manifest: IndexManifest) -> dict[str, Any]:
    return {
        "build_id": manifest.build_id,
        "release": manifest.release_version,
        "embedding_provider": manifest.embedding_provider,
        "embedding_model": manifest.embedding_model,
        "embedding_dimensions": manifest.embedding_dimensions,
        "concept_count": manifest.concept_count,
    }


def _completed_manifest(conn: sqlite3.Connection, build_id: str) -> IndexManifest:
    row = conn.execute(
        "SELECT payload, active FROM manifests WHERE build_id = ? AND state = 'complete'",
        (build_id,),
    ).fetchone()
    if row is None:
        raise IndexEvaluationError("Evaluation build is unavailable; list completed builds")
    return LocalIndex._manifest(row)


def _require_evaluated(manifest: IndexManifest) -> None:
    if manifest.build_kind in {"sample", "legacy"}:
        return
    if manifest.build_kind != "production":
        raise IndexEvaluationError(
            f"Build {manifest.build_id} has an unknown classification; rebuild and evaluate it"
        )
    if not _has_passing_evaluation(manifest):
        raise IndexEvaluationError(
            "Production activation requires a passing evaluation. " + evaluation_next_step(manifest)
        )


def _has_passing_evaluation(manifest: IndexManifest) -> bool:
    report = manifest.evaluation_report or {}
    expected = evaluation_identity(manifest) | {"evaluation_version": manifest.evaluation_version}
    identity_matches = all(report.get(key) == value for key, value in expected.items())
    return report.get("passed") is True and bool(manifest.evaluation_version) and identity_matches


def evaluation_next_step(manifest: IndexManifest) -> str:
    if manifest.unclassified_source_build:
        return (
            f"Snapshot {manifest.unclassified_source_build} is unclassified. "
            f"Evaluate rebuild {manifest.build_id}, or recreate it with index-sample "
            "in a separate data directory."
        )
    return f"Evaluate build {manifest.build_id} with a matching calibration before activation."


def _ranked_page(
    conn: sqlite3.Connection,
    manifest: IndexManifest,
    embedding_provider: EmbeddingProvider,
    query: str,
    limit: int,
    mode: str,
    offset: int = 0,
    retired_status: str | None = None,
) -> tuple[list[SearchHit], int]:
    vector = _query_vector(embedding_provider, manifest, query) if mode != "bm25" else None
    ranked, total = rank_page(conn, manifest, query, vector, mode, offset, limit, retired_status)
    payloads = _concept_payloads(conn, manifest.build_id, [code for code, _ in ranked])
    hits = [
        SearchHit(
            concept=payloads[code],
            score=field.score,
            rank=offset + number,
            score_components={"bm25": field.bm25, "vector": field.vector},
            matched_on=field.kind,
        )
        for number, (code, field) in enumerate(ranked, 1)
    ]
    return hits, total


def _stored_dimensions(
    conn: sqlite3.Connection, manifest: IndexManifest | None
) -> IndexManifest | None:
    if manifest is None or manifest.embedding_dimensions is not None:
        return manifest
    row = conn.execute(
        "SELECT vector, kinds FROM concept_vectors WHERE build_id = ? LIMIT 1", (manifest.build_id,)
    ).fetchone()
    if row:
        return replace(manifest, embedding_dimensions=len(row[0]) // (4 * len(row[1])))
    return manifest


def _concept_payloads(
    conn: sqlite3.Connection, build: str, codes: list[str]
) -> dict[str, NcitConcept]:
    result = {}
    for chunk in batched(codes, _SQL_CHUNK, strict=False):
        for row in conn.execute(
            "SELECT code, payload FROM concepts WHERE build_id = ? "  # noqa: S608
            f"AND code IN ({','.join('?' for _ in chunk)})",
            [build, *chunk],
        ):
            result[row["code"]] = NcitConcept(**json.loads(row["payload"]))
    return result
