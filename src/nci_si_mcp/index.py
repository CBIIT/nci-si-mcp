"""SQLite-backed concept cache and local retrieval index.

Builds are immutable snapshots. Activation retains the previous snapshot for rollback.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from functools import lru_cache
from itertools import batched, chain
from pathlib import Path
from tempfile import TemporaryFile
from uuid import uuid4

from .audit import emit
from .embeddings import EmbeddingProvider
from .errors import (
    IndexBuildError,
    IndexCompatibilityError,
    IndexStorageError,
    NoActiveIndexError,
    PlatformError,
)
from .evs import normalize_concept
from .index_storage import (
    SCHEMA_VERSION,
    build_lease,
    clean_stale_builds,
    concept_fields,
    create_fts,
    delete_build,
    fts_table,
    migrate,
    name_key,
)
from .models import IndexManifest, NcitConcept, SearchHit, Truncation, utc_now_iso
from .retrieval import cosine_similarity, min_max_normalize, tokenize
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

MAX_FTS_CANDIDATES = 1000
# A release of up to this many concepts is scored exactly: every stored vector
# is compared with the query. In pure Python that costs roughly a quarter of a
# second at the limit with 128-dimension vectors (measured on Python 3.14).
EXACT_VECTOR_SCAN_LIMIT = 20_000
# A larger release is narrowed to this many LSH and BM25 candidates first.
MAX_VECTOR_CANDIDATES = 2000
# The LSH layout and the sign hash below are part of the stored format: changing
# them needs a SCHEMA_VERSION bump whose migration rebuilds vector_lsh.
LSH_BANDS = 4
LSH_BITS_PER_BAND = 8
_SQL_CHUNK = 500


def _projection_sign(bit: int, dimension: int) -> float:
    value = ((bit + 1) * 0x9E3779B1) ^ ((dimension + 1) * 0x85EBCA6B)
    value ^= value >> 16
    value = (value * 0x7FEB352D) & 0xFFFFFFFF
    value ^= value >> 15
    return 1.0 if value & 1 else -1.0


@lru_cache(maxsize=8)
def _projection_signs(dimensions: int) -> tuple[tuple[float, ...], ...]:
    return tuple(
        tuple(_projection_sign(bit, dimension) for dimension in range(dimensions))
        for bit in range(LSH_BANDS * LSH_BITS_PER_BAND)
    )


def vector_lsh_buckets(vector: Sequence[float]) -> list[tuple[int, int]]:
    """Return one (band, bucket) pair per band from signed random projections.

    Two vectors land in the same bucket of a band only when all of its
    projection bits agree, so similar vectors share buckets more often.
    """

    signs = _projection_signs(len(vector))
    buckets: list[tuple[int, int]] = []
    for band in range(LSH_BANDS):
        bucket = 0
        for offset in range(LSH_BITS_PER_BAND):
            row = signs[band * LSH_BITS_PER_BAND + offset]
            if sum(value * sign for value, sign in zip(vector, row, strict=True)) >= 0:
                bucket |= 1 << offset
        buckets.append((band, bucket))
    return buckets


_WEIGHTS = {"bm25": (1.0, 0.0), "vector": (0.0, 1.0), "hybrid": (0.55, 0.45)}


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


def _fts_candidate_cap(limit: int) -> int:
    return min(MAX_FTS_CANDIDATES, max(limit * 10, 100))


def _complete(manifest: IndexManifest, mode: str, bm25_count: int, limit: int) -> bool:
    """Whether the concepts scored are every concept that could match the query.

    BM25 reads at most a cap of candidates, and vectors are scored exactly only in an
    index of up to `EXACT_VECTOR_SCAN_LIMIT` concepts.
    """

    if mode == "bm25":
        return bm25_count < _fts_candidate_cap(limit)
    return manifest.concept_count <= EXACT_VECTOR_SCAN_LIMIT


def _results_truncation(scored: int, limit: int, complete: bool) -> Truncation:
    """The truncation record of a search whose `limit` may have left scored concepts out."""

    if scored <= limit:
        return Truncation(occurred=False)
    return Truncation(
        occurred=True,
        bound="results",
        limit=limit,
        reached=limit,
        omitted=scored - limit,
        exact=complete,
    )


def _rank(
    mode: str, bm25_scores: dict[str, float], vector_scores: dict[str, float]
) -> list[tuple[str, float, dict[str, float]]]:
    """Order the scored concepts by the mode's score, best first, ties by code."""

    norm_bm25 = min_max_normalize(bm25_scores)
    norm_vector = min_max_normalize(vector_scores)
    # In bm25 and vector mode the other component was not computed, so its
    # weighted term is exactly zero and the score equals the one component.
    bm25_weight, vector_weight = _WEIGHTS[mode]
    ranked = []
    for code in set(norm_bm25) | set(norm_vector):
        bm25 = norm_bm25.get(code, 0.0)
        vector = norm_vector.get(code, 0.0)
        score = bm25_weight * bm25 + vector_weight * vector
        ranked.append((code, score, {"bm25": bm25, "vector": vector}))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))


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
    for (code, kind, text), vector in zip(fields, vectors, strict=True):
        cursor = conn.execute(
            "INSERT INTO fields(build_id, code, kind, text, vector) VALUES (?, ?, ?, ?, ?)",
            (build_id, code, kind, text, json.dumps(vector)),
        )
        field_id = cursor.lastrowid
        conn.execute(
            f"INSERT INTO {fts_table(build_id)}(rowid, code, kind, text) VALUES (?, ?, ?, ?)",  # noqa: S608
            (field_id, code, kind, text),
        )
        conn.executemany(
            "INSERT INTO vector_lsh VALUES (?, ?, ?, ?)",
            [(build_id, field_id, band, bucket) for band, bucket in vector_lsh_buckets(vector)],
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
        "INSERT INTO concepts VALUES (?, ?, ?, ?)",
        [
            (build_id, item.code, json.dumps(item.to_stored()), name_key(item.preferred_name))
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
        raise IndexBuildError("The active index changed during sample indexing; retry the sample")


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
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version == SCHEMA_VERSION:
                return
            if version > SCHEMA_VERSION:
                raise IndexCompatibilityError(
                    f"Index schema {version} at {self.db_path} "
                    f"is newer than supported {SCHEMA_VERSION}"
                )
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("BEGIN IMMEDIATE")
            migrate(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

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

    def list_builds(self) -> list[IndexManifest]:
        """Completed snapshots, including the rollback target and pending activation."""
        with self._connect() as conn:
            return [
                self._manifest(row)
                for row in conn.execute(
                    "SELECT payload, active FROM manifests WHERE state = 'complete'"
                )
            ]

    def build(
        self,
        raw_concepts: Iterable[dict[str, object]],
        release_date: str | None,
        embedding_provider: EmbeddingProvider,
        expected_release_version: str | None = None,
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
            chain([first], concepts), release_date, embedding_provider, release
        )

    def _build_stream(
        self,
        concepts: Iterable[NcitConcept],
        release_date: str | None,
        provider: EmbeddingProvider,
        release: str,
        compatible: IndexManifest | None = None,
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
            raise IndexBuildError(
                "Unknown completed build; list available builds with index-builds"
            )
        manifest = LocalIndex._manifest(row)
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
                    raise IndexBuildError("Unknown build; list available builds with index-builds")
                old = self._manifest(row)
                for item in conn.execute(
                    "SELECT payload FROM concepts WHERE build_id = ? ORDER BY code", (build_id,)
                ):
                    spool.write(item[0] + "\n")
            spool.seek(0)
            concepts = (NcitConcept(**json.loads(line)) for line in spool)
            return self._build_stream(concepts, old.release_date, provider, old.release_version)

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
        """Rank fields, then concepts, in one read snapshot.

        An exact preferred name ignores case after NFC and whitespace collapsing;
        it scores 1 and wins ties before other hits. Field ties prefer name,
        synonym, definition. Other scores use normalized BM25/vector weights.
        """
        query, limit, mode = validate_search(query, limit, mode, maximum=MAX_INDEX_SEARCH_LIMIT)
        with self._connect() as conn:
            conn.execute("BEGIN")
            manifest = self._searchable_manifest(conn, embedding_provider)
            require_index_release(manifest, requested_release)
            bm25 = (
                self._bm25_scores(conn, manifest.build_id, query, limit) if mode != "vector" else {}
            )
            vectors = (
                self._vector_scores(conn, manifest, embedding_provider, query, set(bm25))
                if mode != "bm25"
                else {}
            )
            hits = _rank_fields(conn, manifest.build_id, query, mode, bm25, vectors)
            truncation = _results_truncation(
                len(hits), limit, _complete(manifest, mode, len(bm25), limit)
            )
            return hits[:limit], truncation, manifest

    def _searchable_manifest(
        self, conn: sqlite3.Connection, provider: EmbeddingProvider
    ) -> IndexManifest:
        manifest = self._active_manifest(conn)
        if manifest is None:
            raise NoActiveIndexError("No active NCIt index is available")
        if manifest.needs_rebuild:
            raise PlatformError(
                "capability_unavailable",
                "Run index-rebuild with this build id, then index-activate, "
                "to enable field search.",
            )
        if not manifest.embedding_matches(provider.name, provider.model):
            raise IndexCompatibilityError(
                "Active index embedding provider/model does not match runtime configuration"
            )
        return manifest

    @staticmethod
    def _bm25_scores(
        conn: sqlite3.Connection, build: str, query: str, limit: int
    ) -> dict[str, float]:
        tokens = tokenize(query)
        if not tokens:
            return {}
        table = fts_table(build)
        rows = conn.execute(
            f"SELECT rowid, -bm25({table}) AS score FROM {table} WHERE {table} MATCH ? "  # noqa: S608
            f"ORDER BY bm25({table}), rowid LIMIT ?",
            (" OR ".join(f'"{token}"' for token in tokens), _fts_candidate_cap(limit)),
        )
        return {str(row["rowid"]): float(row["score"]) for row in rows}

    @staticmethod
    def _vector_scores(
        conn: sqlite3.Connection,
        manifest: IndexManifest,
        provider: EmbeddingProvider,
        query: str,
        bm25_fields: set[str],
    ) -> dict[str, float]:
        vector = _query_vector(provider, manifest, query)
        if manifest.concept_count <= EXACT_VECTOR_SCAN_LIMIT:
            rows = conn.execute(
                "SELECT id, vector FROM fields WHERE build_id = ?", (manifest.build_id,)
            ).fetchall()
        else:
            rows = _candidate_vector_rows(conn, manifest.build_id, vector, bm25_fields)
        scores = {}
        for row in rows:
            stored = json.loads(row["vector"])
            if len(stored) != len(vector):
                raise IndexCompatibilityError(
                    "Stored vector dimensions are inconsistent with the active index"
                )
            scores[str(row["id"])] = cosine_similarity(vector, stored)
        return scores


def _lsh_candidates(
    conn: sqlite3.Connection,
    build: str,
    vector: list[float],
    bm25_fields: set[str],
) -> set[str]:
    buckets = vector_lsh_buckets(vector)
    predicates = " OR ".join("(band = ? AND bucket = ?)" for _ in buckets)
    excluded = ",".join("?" for _ in bm25_fields)
    rows = conn.execute(
        f"SELECT field_id FROM vector_lsh WHERE build_id = ? AND ({predicates}) "  # noqa: S608
        f"AND field_id NOT IN ({excluded}) "
        "GROUP BY field_id ORDER BY COUNT(*) DESC, field_id LIMIT ?",
        [
            build,
            *(value for pair in buckets for value in pair),
            *sorted(bm25_fields),
            max(0, MAX_VECTOR_CANDIDATES - len(bm25_fields)),
        ],
    )
    return bm25_fields | {str(row[0]) for row in rows}


def _candidate_vector_rows(
    conn: sqlite3.Connection, build: str, vector: list[float], bm25_fields: set[str]
) -> list[sqlite3.Row]:
    candidates = _lsh_candidates(conn, build, vector, bm25_fields)
    result = []
    for chunk in batched(sorted(candidates), _SQL_CHUNK, strict=False):
        result.extend(
            conn.execute(
                "SELECT id, vector FROM fields WHERE build_id = ? "  # noqa: S608
                f"AND id IN ({','.join('?' for _ in chunk)})",
                [build, *chunk],
            ).fetchall()
        )
    return result


def _rank_fields(
    conn: sqlite3.Connection,
    build: str,
    query: str,
    mode: str,
    bm25: dict[str, float],
    vectors: dict[str, float],
) -> list[SearchHit]:
    ranked = _rank(mode, bm25, vectors)
    by_code: dict[str, tuple[float, dict[str, float], str]] = {}
    fields = _field_identities(conn, build, [identifier for identifier, _, _ in ranked])
    priority = {"name": 0, "synonym": 1, "definition": 2}
    for identifier, score, components in ranked:
        code, kind = fields[identifier]
        previous = by_code.get(code)
        if previous is None or (-score, priority[kind]) < (-previous[0], priority[previous[2]]):
            by_code[code] = score, components, kind
    return _rank_concepts(conn, build, query, mode, by_code)


def _rank_concepts(
    conn: sqlite3.Connection,
    build: str,
    query: str,
    mode: str,
    by_code: dict[str, tuple[float, dict[str, float], str]],
) -> list[SearchHit]:
    exact = {
        row[0]
        for row in conn.execute(
            "SELECT code FROM concepts WHERE build_id = ? AND name_key = ?",
            (build, name_key(query)),
        )
    }
    for code in exact:
        by_code[code] = (
            1.0,
            {"bm25": float(mode != "vector"), "vector": float(mode != "bm25")},
            "name",
        )
    ordered = sorted(by_code, key=lambda code: (-by_code[code][0], code not in exact, code))
    payloads = _concept_payloads(conn, build, ordered)
    return [
        SearchHit(
            concept=payloads[code],
            score=by_code[code][0],
            rank=rank,
            score_components=by_code[code][1],
            matched_on=by_code[code][2],
        )
        for rank, code in enumerate(ordered, 1)
    ]


def _field_identities(
    conn: sqlite3.Connection, build: str, identifiers: list[str]
) -> dict[str, tuple[str, str]]:
    result = {}
    for chunk in batched(identifiers, _SQL_CHUNK, strict=False):
        for row in conn.execute(
            "SELECT id, code, kind FROM fields WHERE build_id = ? "  # noqa: S608
            f"AND id IN ({','.join('?' for _ in chunk)})",
            [build, *chunk],
        ):
            result[str(row["id"])] = row["code"], row["kind"]
    return result


def _stored_dimensions(
    conn: sqlite3.Connection, manifest: IndexManifest | None
) -> IndexManifest | None:
    if manifest is None or manifest.embedding_dimensions is not None:
        return manifest
    row = conn.execute(
        "SELECT vector FROM fields WHERE build_id = ? LIMIT 1", (manifest.build_id,)
    ).fetchone()
    if row:
        return replace(manifest, embedding_dimensions=len(json.loads(row[0])))
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
