"""SQLite-backed concept cache and local retrieval index."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .embeddings import EmbeddingProvider
from .evs import normalize_concept
from .models import IndexManifest, NcitConcept, SearchHit, utc_now_iso
from .retrieval import BM25Index, cosine_similarity, min_max_normalize


class LocalIndex:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "nci_si.sqlite3"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
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

    def get_active_manifest(self) -> Optional[IndexManifest]:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM manifests WHERE active = 1").fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        return IndexManifest(**payload)

    def set_active_manifest(self, manifest: IndexManifest) -> None:
        payload = manifest.to_dict()
        payload["active"] = True
        with self._connect() as conn:
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

    def upsert_concepts(
        self,
        raw_concepts: Iterable[Dict[str, object]],
        release_date: Optional[str],
        embedding_provider: EmbeddingProvider,
    ) -> IndexManifest:
        rows: List[Tuple[str, str, str, str, str]] = []
        concepts: List[NcitConcept] = []
        for raw in raw_concepts:
            concept = normalize_concept(raw, release_date=release_date, source="active_cache")
            concepts.append(concept)
        search_texts = [concept_search_text(concept) for concept in concepts]
        vectors = embedding_provider.embed(search_texts)
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
        if not rows:
            raise ValueError("No concepts were provided for indexing")
        release_version = rows[0][0]
        if any(row[0] != release_version for row in rows):
            raise ValueError("Cannot mix concept release versions in one index build")
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
            active=True,
        )
        self.set_active_manifest(manifest)
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
        manifest = self.get_active_manifest()
        if not manifest:
            raise RuntimeError("No active NCIt index is available. Build an index first.")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT code, payload, search_text, vector FROM concepts WHERE release_version = ?",
                (manifest.release_version,),
            ).fetchall()
        if not rows:
            return []
        documents = [(row["code"], row["search_text"]) for row in rows]
        bm25_scores = BM25Index(documents).scores(query)
        query_vector = embedding_provider.embed([query])[0]
        vector_scores = {
            row["code"]: cosine_similarity(query_vector, json.loads(row["vector"]))
            for row in rows
        }
        norm_bm25 = min_max_normalize(bm25_scores)
        norm_vector = min_max_normalize(vector_scores)
        combined: Dict[str, Tuple[float, Dict[str, float]]] = {}
        for row in rows:
            code = row["code"]
            bm25 = norm_bm25.get(code, 0.0)
            vector = norm_vector.get(code, 0.0)
            if mode == "bm25":
                score = bm25
            elif mode == "vector":
                score = vector
            else:
                score = 0.55 * bm25 + 0.45 * vector
            combined[code] = (score, {"bm25": bm25, "vector": vector})
        payload_by_code = {row["code"]: json.loads(row["payload"]) for row in rows}
        ranked = sorted(combined.items(), key=lambda item: item[1][0], reverse=True)[:limit]
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
