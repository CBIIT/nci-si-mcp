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

from .embeddings import EmbeddingProvider
from .errors import (
    IndexBuildError,
    IndexCompatibilityError,
    IndexStorageError,
    NoActiveIndexError,
)
from .evs import normalize_concept
from .models import IndexManifest, NcitConcept, SearchHit, utc_now_iso
from .retrieval import cosine_similarity, min_max_normalize, tokenize
from .validation import validate_search

logger = logging.getLogger(__name__)

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS manifests (
                    release_version TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS concepts (
                    release_version TEXT NOT NULL,
                    code TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    vector TEXT NOT NULL,
                    PRIMARY KEY (release_version, code)
                )
                """
            )
            active_rows = conn.execute(
                "SELECT release_version FROM manifests WHERE active = 1 ORDER BY release_version"
            ).fetchall()
            if len(active_rows) > 1:
                keep_release = active_rows[-1]["release_version"]
                conn.execute(
                    "UPDATE manifests SET active = CASE WHEN release_version = ? THEN 1 ELSE 0 END",
                    (keep_release,),
                )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS manifests_single_active
                ON manifests(active) WHERE active = 1
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS concepts_fts USING fts5(
                    release_version UNINDEXED,
                    code UNINDEXED,
                    search_text,
                    tokenize = 'unicode61'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vector_lsh (
                    release_version TEXT NOT NULL,
                    code TEXT NOT NULL,
                    band INTEGER NOT NULL,
                    bucket INTEGER NOT NULL,
                    PRIMARY KEY (release_version, code, band)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS vector_lsh_lookup
                ON vector_lsh(release_version, band, bucket)
                """
            )
            # Earlier versions kept every release ever indexed. Only the active
            # one was reachable, and the others skew BM25 statistics.
            inactive = "release_version NOT IN (SELECT release_version FROM manifests WHERE active = 1)"
            dropped = [
                row[0]
                for row in conn.execute(
                    f"SELECT DISTINCT release_version FROM concepts WHERE {inactive}"
                )
            ]
            for table in ("concepts", "concepts_fts", "vector_lsh", "manifests"):
                conn.execute(f"DELETE FROM {table} WHERE {inactive}")
            if version < 2:
                conn.execute("DELETE FROM concepts_fts")
                conn.execute(
                    """
                    INSERT INTO concepts_fts (release_version, code, search_text)
                    SELECT release_version, code, search_text FROM concepts
                    """
                )
            if version < 3:
                conn.execute("DELETE FROM vector_lsh")
                vector_rows = conn.execute(
                    "SELECT release_version, code, vector FROM concepts"
                ).fetchall()
                conn.executemany(
                    """
                    INSERT INTO vector_lsh (release_version, code, band, bucket)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (row["release_version"], row["code"], band, bucket)
                        for row in vector_rows
                        for band, bucket in vector_lsh_buckets(json.loads(row["vector"]))
                    ],
                )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        if dropped:
            logger.warning("index_migration_dropped_releases releases=%s", ",".join(dropped))

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
        release_version = concepts[0].release_version
        if not release_version:
            raise IndexBuildError("Indexed concepts must include a release version")
        if any(concept.release_version != release_version for concept in concepts):
            raise IndexBuildError("Cannot mix concept release versions in one index build")
        if expected_release_version and release_version != expected_release_version:
            raise IndexCompatibilityError(
                "EVS concept payload release did not match selected monthly release: "
                f"{release_version} != {expected_release_version}"
            )

        search_texts = [concept_search_text(concept) for concept in concepts]
        vectors = embedding_provider.embed(search_texts)
        if len(vectors) != len(concepts):
            raise IndexCompatibilityError("Embedding provider returned an unexpected vector count")
        dimensions = len(vectors[0])
        if dimensions < 1 or any(len(vector) != dimensions for vector in vectors):
            raise IndexCompatibilityError("Embedding provider returned inconsistent vector dimensions")

        codes = [concept.code for concept in concepts]
        keys = [(release_version, code) for code in codes]
        with self._connect() as conn:
            # Take the write lock first so the checks and the writes see one state.
            conn.execute("BEGIN IMMEDIATE")
            active = self._active_manifest(conn)
            if active and active.release_version == release_version:
                stored_dimensions = active.embedding_dimensions or self._stored_dimensions(
                    conn, release_version
                )
                if not active.embedding_matches(
                    embedding_provider.name, embedding_provider.model
                ) or stored_dimensions not in (None, dimensions):
                    raise IndexCompatibilityError(
                        "The active index was built with a different embedding provider, "
                        f"model or dimensions; delete {self.db_path} to rebuild it"
                    )
                # Only codes already indexed have rows to replace. Deleting from
                # the FTS table scans it, so skip the codes that are new.
                replaced = [
                    (release_version, row["code"])
                    for chunk in batched(codes, _SQL_CHUNK, strict=False)
                    for row in conn.execute(
                        "SELECT code FROM concepts WHERE release_version = ? AND code IN "
                        f"({','.join('?' for _ in chunk)})",
                        [release_version, *chunk],
                    )
                ]
                conn.executemany(
                    "DELETE FROM concepts_fts WHERE release_version = ? AND code = ?", replaced
                )
                conn.executemany(
                    "DELETE FROM vector_lsh WHERE release_version = ? AND code = ?", replaced
                )
            else:
                for table in ("concepts", "concepts_fts", "vector_lsh"):
                    conn.execute(f"DELETE FROM {table}")
                if active:
                    logger.info(
                        "index_release_replaced previous=%s new=%s",
                        active.release_version,
                        release_version,
                    )
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
                        release_version,
                        concept.code,
                        json.dumps(concept.to_dict(include_raw=True), sort_keys=True),
                        search_text,
                        json.dumps(vector),
                    )
                    for concept, search_text, vector in zip(concepts, search_texts, vectors, strict=True)
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
        """Rank concepts of the active release by BM25, vector similarity, or both.

        Each component is min-max normalized over the concepts scored for this
        query, so scores rank hits within one result but are not comparable
        across queries. The hybrid score is 0.55 * BM25 + 0.45 * vector.
        """

        query, limit, mode = validate_search(query, limit, mode)
        with self._connect() as conn:
            # One read transaction, so a concurrent re-index cannot change the
            # release between reading the manifest and reading the concepts.
            conn.execute("BEGIN")
            manifest = self._active_manifest(conn)
            if not manifest:
                raise NoActiveIndexError(
                    "No active NCIt index is available; build one with the index-sample command"
                )
            if not manifest.embedding_matches(embedding_provider.name, embedding_provider.model):
                raise IndexCompatibilityError(
                    "Active index embedding provider/model does not match runtime configuration"
                )
            release = manifest.release_version
            bm25_scores: dict[str, float] = {}
            if mode != "vector":
                bm25_scores = self._bm25_scores(conn, release, query, limit)
            vector_scores: dict[str, float] = {}
            if mode != "bm25":
                vector_scores = self._vector_scores(
                    conn, manifest, embedding_provider, query, set(bm25_scores)
                )

            norm_bm25 = min_max_normalize(bm25_scores)
            norm_vector = min_max_normalize(vector_scores)
            combined: dict[str, tuple[float, dict[str, float]]] = {}
            for code in set(norm_bm25) | set(norm_vector):
                bm25 = norm_bm25.get(code, 0.0)
                vector = norm_vector.get(code, 0.0)
                if mode == "bm25":
                    score = bm25
                elif mode == "vector":
                    score = vector
                else:
                    score = 0.55 * bm25 + 0.45 * vector
                combined[code] = (score, {"bm25": bm25, "vector": vector})
            ranked = sorted(combined.items(), key=lambda item: (-item[1][0], item[0]))[:limit]
            placeholders = ",".join("?" for _ in ranked)
            payload_by_code = {
                row["code"]: json.loads(row["payload"])
                for row in conn.execute(
                    f"SELECT code, payload FROM concepts WHERE release_version = ? "
                    f"AND code IN ({placeholders})",
                    [release, *(code for code, _ in ranked)],
                )
            }
        return [
            SearchHit(
                concept=NcitConcept(**payload_by_code[code]),
                score=float(score),
                rank=rank,
                score_components=components,
            )
            for rank, (code, (score, components)) in enumerate(ranked, start=1)
        ]

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
                min(MAX_FTS_CANDIDATES, max(limit * 10, 100)),
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
        query_vectors = embedding_provider.embed([query])
        if len(query_vectors) != 1:
            raise IndexCompatibilityError("Embedding provider returned an unexpected query vector count")
        query_vector = query_vectors[0]
        if not manifest.embedding_matches(
            embedding_provider.name, embedding_provider.model, len(query_vector)
        ):
            raise IndexCompatibilityError("Query embedding dimensions do not match the active index")

        release = manifest.release_version
        if manifest.concept_count <= EXACT_VECTOR_SCAN_LIMIT:
            rows = conn.execute(
                "SELECT code, vector FROM concepts WHERE release_version = ?", (release,)
            ).fetchall()
        else:
            # Approximate: prefer the concepts that share the most LSH bands with
            # the query, and always score the BM25 candidates as well.
            buckets = vector_lsh_buckets(query_vector)
            predicates = " OR ".join("(band = ? AND bucket = ?)" for _ in buckets)
            lsh_rows = conn.execute(
                f"SELECT code FROM vector_lsh WHERE release_version = ? AND ({predicates}) "
                "GROUP BY code ORDER BY COUNT(*) DESC, code LIMIT ?",
                [
                    release,
                    *(value for pair in buckets for value in pair),
                    MAX_VECTOR_CANDIDATES - len(bm25_codes),
                ],
            ).fetchall()
            rows = []
            candidates = sorted(bm25_codes.union(row["code"] for row in lsh_rows))
            for chunk in batched(candidates, _SQL_CHUNK, strict=False):
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        f"SELECT code, vector FROM concepts WHERE release_version = ? "
                        f"AND code IN ({placeholders})",
                        [release, *chunk],
                    ).fetchall()
                )

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
