"""SQLite-backed concept cache and local retrieval index."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .embeddings import EmbeddingProvider
from .errors import IndexCompatibilityError
from .evs import normalize_concept
from .models import IndexManifest, NcitConcept, SearchHit, utc_now_iso
from .retrieval import cosine_similarity, min_max_normalize, tokenize

SCHEMA_VERSION = 3
SEARCH_MODES = frozenset({"bm25", "vector", "hybrid"})
MAX_FTS_CANDIDATES = 1000
MAX_VECTOR_CANDIDATES = 2000
LSH_BANDS = 4
LSH_BITS_PER_BAND = 8


def _projection_sign(bit: int, dimension: int) -> float:
    value = ((bit + 1) * 0x9E3779B1) ^ ((dimension + 1) * 0x85EBCA6B)
    value ^= value >> 16
    value = (value * 0x7FEB352D) & 0xFFFFFFFF
    value ^= value >> 15
    return 1.0 if value & 1 else -1.0


def vector_lsh_buckets(vector: List[float]) -> List[Tuple[int, int]]:
    buckets: List[Tuple[int, int]] = []
    for band in range(LSH_BANDS):
        bucket = 0
        for offset in range(LSH_BITS_PER_BAND):
            bit = band * LSH_BITS_PER_BAND + offset
            projection = sum(
                value * _projection_sign(bit, dimension)
                for dimension, value in enumerate(vector)
            )
            if projection >= 0:
                bucket |= 1 << offset
        buckets.append((band, bucket))
    return buckets


class LocalIndex:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "nci_si.sqlite3"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Index schema {version} is newer than supported schema {SCHEMA_VERSION}"
                )
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
                lsh_rows = []
                for row in vector_rows:
                    for band, bucket in vector_lsh_buckets(json.loads(row["vector"])):
                        lsh_rows.append(
                            (row["release_version"], row["code"], band, bucket)
                        )
                conn.executemany(
                    """
                    INSERT INTO vector_lsh (release_version, code, band, bucket)
                    VALUES (?, ?, ?, ?)
                    """,
                    lsh_rows,
                )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def get_active_manifest(self) -> Optional[IndexManifest]:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM manifests WHERE active = 1").fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        return IndexManifest(**payload)

    def get_manifest(self, release_version: str) -> Optional[IndexManifest]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload, active FROM manifests WHERE release_version = ?",
                (release_version,),
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        payload["active"] = bool(row["active"])
        return IndexManifest(**payload)

    @staticmethod
    def _set_active_manifest(conn: sqlite3.Connection, manifest: IndexManifest) -> None:
        payload = manifest.to_dict()
        payload["active"] = True
        conn.execute("UPDATE manifests SET active = 0")
        conn.execute(
            """
            INSERT INTO manifests (release_version, payload, active)
            VALUES (?, ?, 1)
            ON CONFLICT(release_version)
            DO UPDATE SET payload = excluded.payload, active = 1
            """,
            (manifest.release_version, json.dumps(payload, sort_keys=True)),
        )

    def set_active_manifest(self, manifest: IndexManifest) -> None:
        with self._connect() as conn:
            self._set_active_manifest(conn, manifest)

    def upsert_concepts(
        self,
        raw_concepts: Iterable[Dict[str, object]],
        release_date: Optional[str],
        embedding_provider: EmbeddingProvider,
        expected_release_version: Optional[str] = None,
    ) -> IndexManifest:
        concepts: List[NcitConcept] = []
        for raw in raw_concepts:
            concept = normalize_concept(raw, release_date=release_date, source="active_cache")
            concepts.append(concept)
        if not concepts:
            raise ValueError("No concepts were provided for indexing")
        release_version = concepts[0].release_version
        if not release_version:
            raise ValueError("Indexed concepts must include a release version")
        if any(concept.release_version != release_version for concept in concepts):
            raise ValueError("Cannot mix concept release versions in one index build")
        if expected_release_version and release_version != expected_release_version:
            raise IndexCompatibilityError(
                "EVS concept payload release did not match selected monthly release: "
                f"{release_version} != {expected_release_version}"
            )

        existing_manifest = self.get_manifest(release_version)
        if existing_manifest and (
            existing_manifest.embedding_provider != embedding_provider.name
            or existing_manifest.embedding_model != embedding_provider.model
        ):
            raise IndexCompatibilityError(
                "Cannot incrementally update a release with a different embedding provider or model"
            )

        search_texts = [concept_search_text(concept) for concept in concepts]
        vectors = embedding_provider.embed(search_texts)
        if len(vectors) != len(concepts):
            raise IndexCompatibilityError("Embedding provider returned an unexpected vector count")
        dimensions = len(vectors[0]) if vectors else 0
        if dimensions < 1 or any(len(vector) != dimensions for vector in vectors):
            raise IndexCompatibilityError("Embedding provider returned inconsistent vector dimensions")
        if (
            existing_manifest
            and existing_manifest.embedding_dimensions is not None
            and existing_manifest.embedding_dimensions != dimensions
        ):
            raise IndexCompatibilityError(
                "Cannot incrementally update a release with different embedding dimensions"
            )

        rows: List[Tuple[str, str, str, str, str]] = []
        for concept, search_text, vector in zip(concepts, search_texts, vectors):
            rows.append(
                (
                    concept.release_version,
                    concept.code,
                    json.dumps(concept.to_dict(include_raw=True), sort_keys=True),
                    search_text,
                    json.dumps(vector),
                )
            )
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO concepts (release_version, code, payload, search_text, vector)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(release_version, code)
                DO UPDATE SET payload = excluded.payload,
                              search_text = excluded.search_text,
                              vector = excluded.vector
                """,
                rows,
            )
            conn.executemany(
                "DELETE FROM concepts_fts WHERE release_version = ? AND code = ?",
                [(row[0], row[1]) for row in rows],
            )
            conn.executemany(
                """
                INSERT INTO concepts_fts (release_version, code, search_text)
                VALUES (?, ?, ?)
                """,
                [(row[0], row[1], row[3]) for row in rows],
            )
            conn.executemany(
                "DELETE FROM vector_lsh WHERE release_version = ? AND code = ?",
                [(row[0], row[1]) for row in rows],
            )
            lsh_rows = []
            for concept, vector in zip(concepts, vectors):
                for band, bucket in vector_lsh_buckets(vector):
                    lsh_rows.append(
                        (concept.release_version, concept.code, band, bucket)
                    )
            conn.executemany(
                """
                INSERT INTO vector_lsh (release_version, code, band, bucket)
                VALUES (?, ?, ?, ?)
                """,
                lsh_rows,
            )
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM concepts WHERE release_version = ?",
                (release_version,),
            ).fetchone()["count"]
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
            self._set_active_manifest(conn, manifest)
        return manifest

    def get_concept(self, code: str, release_version: Optional[str] = None) -> Optional[NcitConcept]:
        manifest = self.get_active_manifest()
        selected_release = release_version or (manifest.release_version if manifest else None)
        if not selected_release:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM concepts WHERE release_version = ? AND code = ?",
                (selected_release, code),
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        payload["source"] = "active_cache"
        payload["retrieved_at"] = utc_now_iso()
        return NcitConcept(**payload)

    def search(
        self,
        query: str,
        embedding_provider: EmbeddingProvider,
        limit: int = 10,
        mode: str = "hybrid",
    ) -> List[SearchHit]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Search query must not be blank")
        if mode not in SEARCH_MODES:
            raise ValueError(f"Unknown search mode: {mode}")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("Search limit must be a positive integer")
        manifest = self.get_active_manifest()
        if not manifest:
            raise RuntimeError("No active NCIt index is available. Build an index first.")
        if (
            manifest.embedding_provider != embedding_provider.name
            or manifest.embedding_model != embedding_provider.model
        ):
            raise IndexCompatibilityError(
                "Active index embedding provider/model does not match runtime configuration"
            )

        bm25_scores: Dict[str, float] = {}
        vector_scores: Dict[str, float] = {}
        with self._connect() as conn:
            if mode in {"bm25", "hybrid"}:
                tokens = tokenize(normalized_query)
                if tokens:
                    match_query = " OR ".join(f'"{token}"' for token in tokens)
                    candidate_limit = min(MAX_FTS_CANDIDATES, max(limit * 10, 100))
                    fts_rows = conn.execute(
                        """
                        SELECT code, -bm25(concepts_fts) AS score
                        FROM concepts_fts
                        WHERE concepts_fts MATCH ? AND release_version = ?
                        ORDER BY bm25(concepts_fts)
                        LIMIT ?
                        """,
                        (match_query, manifest.release_version, candidate_limit),
                    ).fetchall()
                    bm25_scores = {row["code"]: float(row["score"]) for row in fts_rows}

            if mode in {"vector", "hybrid"}:
                query_vectors = embedding_provider.embed([normalized_query])
                if len(query_vectors) != 1:
                    raise IndexCompatibilityError(
                        "Embedding provider returned an unexpected query vector count"
                    )
                query_vector = query_vectors[0]
                if (
                    manifest.embedding_dimensions is not None
                    and len(query_vector) != manifest.embedding_dimensions
                ):
                    raise IndexCompatibilityError(
                        "Query embedding dimensions do not match the active index"
                    )
                buckets = vector_lsh_buckets(query_vector)
                bucket_predicates = " OR ".join(
                    "(band = ? AND bucket = ?)" for _ in buckets
                )
                bucket_parameters: List[object] = [manifest.release_version]
                for band, bucket in buckets:
                    bucket_parameters.extend([band, bucket])
                lsh_limit = MAX_VECTOR_CANDIDATES
                if mode == "hybrid":
                    lsh_limit = max(1, MAX_VECTOR_CANDIDATES - len(bm25_scores))
                bucket_parameters.append(lsh_limit)
                candidate_rows = conn.execute(
                    f"SELECT DISTINCT code FROM vector_lsh "
                    f"WHERE release_version = ? AND ({bucket_predicates}) LIMIT ?",
                    bucket_parameters,
                ).fetchall()
                candidate_codes = {row["code"] for row in candidate_rows}
                if mode == "hybrid":
                    candidate_codes.update(bm25_scores)
                if not candidate_codes:
                    fallback_rows = conn.execute(
                        """
                        SELECT code FROM concepts
                        WHERE release_version = ? ORDER BY code LIMIT ?
                        """,
                        (manifest.release_version, MAX_VECTOR_CANDIDATES),
                    ).fetchall()
                    candidate_codes = {row["code"] for row in fallback_rows}

                vector_rows = []
                candidate_list = sorted(candidate_codes)[:MAX_VECTOR_CANDIDATES]
                for offset in range(0, len(candidate_list), 500):
                    chunk = candidate_list[offset : offset + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    vector_rows.extend(
                        conn.execute(
                            f"SELECT code, vector FROM concepts WHERE release_version = ? "
                            f"AND code IN ({placeholders})",
                            [manifest.release_version, *chunk],
                        ).fetchall()
                    )
                for row in vector_rows:
                    stored_vector = json.loads(row["vector"])
                    if len(stored_vector) != len(query_vector):
                        raise IndexCompatibilityError(
                            "Stored vector dimensions are inconsistent with the active index"
                        )
                    vector_scores[row["code"]] = cosine_similarity(
                        query_vector, stored_vector
                    )

        norm_bm25 = min_max_normalize(bm25_scores)
        norm_vector = min_max_normalize(vector_scores)
        combined: Dict[str, Tuple[float, Dict[str, float]]] = {}
        candidate_codes = set(norm_bm25) | set(norm_vector)
        for code in candidate_codes:
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
        if not ranked:
            return []
        ranked_codes = [code for code, _ in ranked]
        placeholders = ",".join("?" for _ in ranked_codes)
        with self._connect() as conn:
            payload_rows = conn.execute(
                f"SELECT code, payload FROM concepts WHERE release_version = ? "
                f"AND code IN ({placeholders})",
                [manifest.release_version, *ranked_codes],
            ).fetchall()
        payload_by_code = {
            row["code"]: json.loads(row["payload"]) for row in payload_rows
        }
        hits: List[SearchHit] = []
        for rank, (code, (score, components)) in enumerate(ranked, start=1):
            payload = payload_by_code[code]
            payload["source"] = "active_cache"
            payload["retrieved_at"] = utc_now_iso()
            hits.append(
                SearchHit(
                    concept=NcitConcept(**payload),
                    score=float(score),
                    rank=rank,
                    score_components=components,
                )
            )
        return hits


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
