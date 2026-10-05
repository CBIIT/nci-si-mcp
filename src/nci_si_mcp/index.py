"""SQLite-backed concept cache and local retrieval index.

The database holds one NCIt release at a time. Indexing concepts from a
different release replaces the previous one in the same transaction.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from itertools import batched
from pathlib import Path

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

SCHEMA_VERSION = 4
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
# The schema versions that introduced the FTS table and the LSH table.
_FTS_SCHEMA = 2
_LSH_SCHEMA = 3


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


_CONCEPT_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS manifests (
        release_version TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS concepts (
        release_version TEXT NOT NULL,
        code TEXT NOT NULL,
        payload TEXT NOT NULL,
        search_text TEXT NOT NULL,
        vector TEXT NOT NULL,
        PRIMARY KEY (release_version, code)
    )
    """,
)
_SEARCH_TABLES = (
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS concepts_fts USING fts5(
        release_version UNINDEXED,
        code UNINDEXED,
        search_text,
        tokenize = 'unicode61'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vector_lsh (
        release_version TEXT NOT NULL,
        code TEXT NOT NULL,
        band INTEGER NOT NULL,
        bucket INTEGER NOT NULL,
        PRIMARY KEY (release_version, code, band)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS vector_lsh_lookup
    ON vector_lsh(release_version, band, bucket)
    """,
)
# Rank weights of the BM25 and the vector component in each search mode.
_WEIGHTS = {"bm25": (1.0, 0.0), "vector": (0.0, 1.0), "hybrid": (0.55, 0.45)}


def _create_schema(conn: sqlite3.Connection) -> None:
    for statement in _CONCEPT_TABLES:
        conn.execute(statement)
    # Databases of the first prototype could mark several releases active.
    active_rows = conn.execute(
        "SELECT release_version FROM manifests WHERE active = 1 ORDER BY release_version"
    ).fetchall()
    if len(active_rows) > 1:
        conn.execute(
            "UPDATE manifests SET active = CASE WHEN release_version = ? THEN 1 ELSE 0 END",
            (active_rows[-1]["release_version"],),
        )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS manifests_single_active
        ON manifests(active) WHERE active = 1
        """
    )
    for statement in _SEARCH_TABLES:
        conn.execute(statement)


def _drop_inactive_releases(conn: sqlite3.Connection) -> list[str]:
    """Delete the rows of every release but the active one and return those releases.

    Earlier versions kept every release ever indexed. Only the active one was
    reachable, and the others skew BM25 statistics.
    """

    inactive = "release_version NOT IN (SELECT release_version FROM manifests WHERE active = 1)"
    dropped = [
        row[0]
        for row in conn.execute(
            f"SELECT DISTINCT release_version FROM concepts WHERE {inactive}"  # noqa: S608
        )
    ]
    for table in ("concepts", "concepts_fts", "vector_lsh", "manifests"):
        conn.execute(f"DELETE FROM {table} WHERE {inactive}")  # noqa: S608
    return dropped


def _backfill_search_tables(conn: sqlite3.Connection, version: int) -> None:
    """Rebuild the tables that a database of schema `version` does not have filled."""

    if version < _FTS_SCHEMA:
        conn.execute("DELETE FROM concepts_fts")
        conn.execute(
            """
            INSERT INTO concepts_fts (release_version, code, search_text)
            SELECT release_version, code, search_text FROM concepts
            """
        )
    if version < _LSH_SCHEMA:
        conn.execute("DELETE FROM vector_lsh")
        vector_rows = conn.execute("SELECT release_version, code, vector FROM concepts").fetchall()
        conn.executemany(
            "INSERT INTO vector_lsh (release_version, code, band, bucket) VALUES (?, ?, ?, ?)",
            [
                (row["release_version"], row["code"], band, bucket)
                for row in vector_rows
                for band, bucket in vector_lsh_buckets(json.loads(row["vector"]))
            ],
        )


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


def _remove_search_rows(conn: sqlite3.Connection, release_version: str, codes: list[str]) -> None:
    """Delete the FTS and LSH rows of the codes that are already indexed.

    Deleting from the FTS table scans it, so the codes that are new are skipped.
    """

    replaced = [
        (release_version, row["code"])
        for chunk in batched(codes, _SQL_CHUNK, strict=False)
        for row in conn.execute(
            "SELECT code FROM concepts WHERE release_version = ? AND code IN "  # noqa: S608
            f"({','.join('?' for _ in chunk)})",
            [release_version, *chunk],
        )
    ]
    conn.executemany("DELETE FROM concepts_fts WHERE release_version = ? AND code = ?", replaced)
    conn.executemany("DELETE FROM vector_lsh WHERE release_version = ? AND code = ?", replaced)


def _insert_rows(
    conn: sqlite3.Connection,
    concepts: list[NcitConcept],
    search_texts: list[str],
    vectors: list[list[float]],
) -> None:
    keys = [(concept.release_version, concept.code) for concept in concepts]
    conn.executemany(
        """
        INSERT INTO concepts (release_version, code, payload, search_text, vector)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(release_version, code)
        DO UPDATE SET payload = excluded.payload,
                      search_text = excluded.search_text,
                      vector = excluded.vector
        """,
        [
            (
                *key,
                json.dumps(concept.to_stored(), sort_keys=True),
                search_text,
                json.dumps(vector),
            )
            for key, concept, search_text, vector in zip(
                keys, concepts, search_texts, vectors, strict=True
            )
        ],
    )
    conn.executemany(
        "INSERT INTO concepts_fts (release_version, code, search_text) VALUES (?, ?, ?)",
        [(*key, search_text) for key, search_text in zip(keys, search_texts, strict=True)],
    )
    conn.executemany(
        "INSERT INTO vector_lsh (release_version, code, band, bucket) VALUES (?, ?, ?, ?)",
        [
            (*key, band, bucket)
            for key, vector in zip(keys, vectors, strict=True)
            for band, bucket in vector_lsh_buckets(vector)
        ],
    )


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


def _candidate_vector_rows(
    conn: sqlite3.Connection, release: str, query_vector: list[float], bm25_codes: set[str]
) -> list[sqlite3.Row]:
    """The stored vectors worth scoring in a release too large to scan.

    Approximate: prefer the concepts that share the most LSH bands with the
    query, and always include the BM25 candidates as well.
    """

    buckets = vector_lsh_buckets(query_vector)
    predicates = " OR ".join("(band = ? AND bucket = ?)" for _ in buckets)
    lsh_rows = conn.execute(
        f"SELECT code FROM vector_lsh WHERE release_version = ? AND ({predicates}) "  # noqa: S608
        "GROUP BY code ORDER BY COUNT(*) DESC, code LIMIT ?",
        [
            release,
            *(value for pair in buckets for value in pair),
            MAX_VECTOR_CANDIDATES - len(bm25_codes),
        ],
    ).fetchall()
    rows: list[sqlite3.Row] = []
    candidates = sorted(bm25_codes.union(row["code"] for row in lsh_rows))
    for chunk in batched(candidates, _SQL_CHUNK, strict=False):
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(
            conn.execute(
                f"SELECT code, vector FROM concepts WHERE release_version = ? "  # noqa: S608
                f"AND code IN ({placeholders})",
                [release, *chunk],
            ).fetchall()
        )
    return rows


class LocalIndex:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "nci_si.sqlite3"
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection, commit or roll back its transaction, then close it.

        Failures of the database itself (locked, unreadable, not a database,
        disk full) are raised as IndexStorageError naming the file. Constraint
        violations and API misuse (IntegrityError, ProgrammingError) are bugs
        and propagate unchanged. sqlite3 reports a mistake in a SQL statement
        (unknown table, syntax error) as OperationalError as well, so that
        surfaces as IndexStorageError too.
        """

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
                    f"Index schema {version} at {self.db_path} is newer than the "
                    f"supported schema {SCHEMA_VERSION}"
                )
            conn.execute("PRAGMA journal_mode = WAL")
            _create_schema(conn)
            dropped = _drop_inactive_releases(conn)
            _backfill_search_tables(conn, version)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        if dropped:
            emit(logger, logging.WARNING, "index_migration_dropped_releases", releases=dropped)

    @staticmethod
    def _active_manifest(conn: sqlite3.Connection) -> IndexManifest | None:
        row = conn.execute("SELECT payload FROM manifests WHERE active = 1").fetchone()
        return IndexManifest.from_payload(json.loads(row["payload"])) if row else None

    def get_active_manifest(self) -> IndexManifest | None:
        with self._connect() as conn:
            return self._active_manifest(conn)

    def upsert_concepts(
        self,
        raw_concepts: Iterable[dict[str, object]],
        release_date: str | None,
        embedding_provider: EmbeddingProvider,
        expected_release_version: str | None = None,
    ) -> IndexManifest:
        """Add concepts of one release to the index and make that release active.

        Concepts of the active release are added to it, provided the embedding
        space is the same. Concepts of any other release replace the index.
        Every check runs before the first write, and all writes share one
        transaction, so a failure leaves the previous index untouched.
        """

        concepts = _distinct_concepts(raw_concepts, release_date)
        release_version = _single_release(concepts)
        if expected_release_version and release_version != expected_release_version:
            raise IndexCompatibilityError(
                "EVS concept payload release did not match selected monthly release: "
                f"{release_version} != {expected_release_version}"
            )
        search_texts = [concept_search_text(concept) for concept in concepts]
        vectors = _embed(embedding_provider, search_texts)
        with self._connect() as conn:
            # Take the write lock first so the checks and the writes see one state.
            conn.execute("BEGIN IMMEDIATE")
            active = self._active_manifest(conn)
            if active and active.release_version == release_version:
                self._require_same_embedding_space(conn, active, embedding_provider, vectors)
                _remove_search_rows(conn, release_version, [concept.code for concept in concepts])
            else:
                self._clear(conn, active, release_version)
            _insert_rows(conn, concepts, search_texts, vectors)
            return self._activate(
                conn, release_version, release_date, embedding_provider, len(vectors[0])
            )

    def _require_same_embedding_space(
        self,
        conn: sqlite3.Connection,
        active: IndexManifest,
        embedding_provider: EmbeddingProvider,
        vectors: list[list[float]],
    ) -> None:
        stored_dimensions = active.embedding_dimensions or self._stored_dimensions(
            conn, active.release_version
        )
        same_model = active.embedding_matches(embedding_provider.name, embedding_provider.model)
        if not same_model or stored_dimensions not in (None, len(vectors[0])):
            raise IndexCompatibilityError(
                "The active index was built with a different embedding provider, "
                f"model or dimensions; delete {self.db_path} to rebuild it"
            )

    @staticmethod
    def _clear(
        conn: sqlite3.Connection, active: IndexManifest | None, release_version: str
    ) -> None:
        for table in ("concepts", "concepts_fts", "vector_lsh"):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608
        if active:
            emit(
                logger,
                logging.INFO,
                "index_release_replaced",
                previous=active.release_version,
                release=release_version,
            )

    def _activate(
        self,
        conn: sqlite3.Connection,
        release_version: str,
        release_date: str | None,
        embedding_provider: EmbeddingProvider,
        dimensions: int,
    ) -> IndexManifest:
        """Replace the manifest with one that describes the index as it is now."""

        count = conn.execute(
            "SELECT COUNT(*) FROM concepts WHERE release_version = ?", (release_version,)
        ).fetchone()[0]
        manifest = IndexManifest(
            terminology="ncit",
            release_version=release_version,
            release_date=release_date,
            embedding_provider=embedding_provider.name,
            embedding_model=embedding_provider.model,
            concept_count=int(count),
            built_at=utc_now_iso(),
            index_path=str(self.db_path),
            embedding_dimensions=dimensions,
            active=True,
        )
        conn.execute("DELETE FROM manifests")
        conn.execute(
            "INSERT INTO manifests (release_version, payload, active) VALUES (?, ?, 1)",
            (release_version, json.dumps(manifest.to_dict(), sort_keys=True)),
        )
        return manifest

    @staticmethod
    def _stored_dimensions(conn: sqlite3.Connection, release_version: str) -> int | None:
        """Vector length of an index built before manifests recorded dimensions."""

        row = conn.execute(
            "SELECT vector FROM concepts WHERE release_version = ? LIMIT 1", (release_version,)
        ).fetchone()
        return len(json.loads(row["vector"])) if row else None

    def get_concept(self, code: str) -> NcitConcept | None:
        """Return a concept of the active release as it was fetched at index time."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT concepts.payload FROM concepts
                JOIN manifests ON manifests.release_version = concepts.release_version
                WHERE manifests.active = 1 AND concepts.code = ?
                """,
                (code,),
            ).fetchone()
        return NcitConcept(**json.loads(row["payload"])) if row else None

    def search(
        self,
        query: str,
        embedding_provider: EmbeddingProvider,
        limit: int = 10,
        mode: str = "hybrid",
    ) -> list[SearchHit]:
        """The hits of `search_with_truncation`, without its truncation record."""

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
        """Rank concepts of the active release by BM25, vector similarity, or both.

        Each component is min-max normalized over the concepts scored for this
        query, so scores rank hits within one result but are not comparable
        across queries. The hybrid score is 0.55 * BM25 + 0.45 * vector. The
        truncation record says how many scored concepts the limit left out.
        """

        query, limit, mode = validate_search(query, limit, mode, maximum=MAX_INDEX_SEARCH_LIMIT)
        with self._connect() as conn:
            # One read transaction, so a concurrent re-index cannot change the
            # release between reading the manifest and reading the concepts.
            conn.execute("BEGIN")
            manifest = self._searchable_manifest(conn, embedding_provider)
            release = manifest.release_version
            require_index_release(manifest, requested_release)
            bm25_scores: dict[str, float] = {}
            if mode != "vector":
                bm25_scores = self._bm25_scores(conn, release, query, limit)
            vector_scores: dict[str, float] = {}
            if mode != "bm25":
                vector_scores = self._vector_scores(
                    conn, manifest, embedding_provider, query, set(bm25_scores)
                )
            scored = _rank(mode, bm25_scores, vector_scores)
            ranked = scored[:limit]
            truncation = _results_truncation(
                len(scored), limit, _complete(manifest, mode, len(bm25_scores), limit)
            )
            placeholders = ",".join("?" for _ in ranked)
            payload_by_code = {
                row["code"]: json.loads(row["payload"])
                for row in conn.execute(
                    f"SELECT code, payload FROM concepts WHERE release_version = ? "  # noqa: S608
                    f"AND code IN ({placeholders})",
                    [release, *(code for code, _, _ in ranked)],
                )
            }
        hits = [
            SearchHit(
                concept=NcitConcept(**payload_by_code[code]),
                score=float(score),
                rank=rank,
                score_components=components,
            )
            for rank, (code, score, components) in enumerate(ranked, start=1)
        ]
        return hits, truncation

    def _searchable_manifest(
        self, conn: sqlite3.Connection, embedding_provider: EmbeddingProvider
    ) -> IndexManifest:
        manifest = self._active_manifest(conn)
        if not manifest:
            raise NoActiveIndexError("No active NCIt index is available")
        if not manifest.embedding_matches(embedding_provider.name, embedding_provider.model):
            raise IndexCompatibilityError(
                "Active index embedding provider/model does not match runtime configuration"
            )
        return manifest

    @staticmethod
    def _bm25_scores(
        conn: sqlite3.Connection, release: str, query: str, limit: int
    ) -> dict[str, float]:
        tokens = tokenize(query)
        if not tokens:
            return {}
        rows = conn.execute(
            """
            SELECT code, -bm25(concepts_fts) AS score
            FROM concepts_fts
            WHERE concepts_fts MATCH ? AND release_version = ?
            ORDER BY bm25(concepts_fts)
            LIMIT ?
            """,
            (
                " OR ".join(f'"{token}"' for token in tokens),
                release,
                _fts_candidate_cap(limit),
            ),
        ).fetchall()
        return {row["code"]: float(row["score"]) for row in rows}

    @staticmethod
    def _vector_scores(
        conn: sqlite3.Connection,
        manifest: IndexManifest,
        embedding_provider: EmbeddingProvider,
        query: str,
        bm25_codes: set[str],
    ) -> dict[str, float]:
        query_vector = _query_vector(embedding_provider, manifest, query)
        release = manifest.release_version
        if manifest.concept_count <= EXACT_VECTOR_SCAN_LIMIT:
            rows = conn.execute(
                "SELECT code, vector FROM concepts WHERE release_version = ?", (release,)
            ).fetchall()
        else:
            rows = _candidate_vector_rows(conn, release, query_vector, bm25_codes)

        scores: dict[str, float] = {}
        for row in rows:
            stored_vector = json.loads(row["vector"])
            if len(stored_vector) != len(query_vector):
                raise IndexCompatibilityError(
                    "Stored vector dimensions are inconsistent with the active index"
                )
            scores[row["code"]] = cosine_similarity(query_vector, stored_vector)
        return scores


def concept_search_text(concept: NcitConcept) -> str:
    definitions = " ".join(
        item.get("definition", "") for item in concept.evidence.get("definitions", [])
    )
    synonyms = " ".join(item.get("name", "") for item in concept.evidence.get("synonyms", []))
    semantic_types = " ".join(concept.evidence.get("semantic_types", []))
    sources = " ".join(concept.evidence.get("contributing_sources", []))
    return " ".join(
        part
        for part in [
            concept.code,
            concept.preferred_name,
            synonyms,
            definitions,
            semantic_types,
            sources,
        ]
        if part
    )
