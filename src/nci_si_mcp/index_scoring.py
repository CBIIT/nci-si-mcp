"""Exact field scoring with one vector scan and compact per-query numeric arrays."""

from __future__ import annotations

import heapq
import re
import sqlite3
from collections.abc import Iterator
from typing import Any, NamedTuple

from .errors import IndexCompatibilityError, IndexStorageError, PlatformError
from .index_storage import FIELD_KINDS, fts_table, name_key
from .models import IndexManifest

CHUNK_SIZE = 2048
_PRIORITY = {kind: number for number, kind in enumerate(FIELD_KINDS)}
_WEIGHTS = {"bm25": (1.0, 0.0), "vector": (0.0, 1.0), "hybrid": (0.55, 0.45)}


class FieldScore(NamedTuple):
    score: float
    bm25: float
    vector: float
    kind: str


def _numpy() -> Any:
    try:
        import numpy
    except ImportError:
        raise PlatformError(
            "capability_unavailable",
            "Exact indexed search needs NumPy. Install the 'index' extra and retry.",
            capability="semantic/hybrid index scoring",
        ) from None
    return numpy


def _bm25(conn: sqlite3.Connection, build: str, query: str, mode: str) -> tuple[float, float]:
    # SQLite's temporary storage is file-backed; matching field scores are never
    # collected as a full Python ranking or retained between calls.
    conn.execute("CREATE TEMP TABLE query_bm25 (id INTEGER PRIMARY KEY, score REAL NOT NULL)")
    # unicode61 folds case and diacritics; quote every token so FTS syntax is inert.
    tokens = re.findall(r"\w+", query.lower())
    if mode != "vector" and tokens:
        table = fts_table(build)
        conn.execute(
            f"INSERT INTO query_bm25 SELECT rowid, -bm25({table}) FROM {table} "  # noqa: S608
            f"WHERE {table} MATCH ?",
            (" OR ".join(f'"{token}"' for token in tokens),),
        )
    row = conn.execute("SELECT min(score), max(score) FROM query_bm25").fetchone()
    return row[0] or 0.0, row[1] or 0.0


def _bm25_rows(
    conn: sqlite3.Connection,
    build: str,
    status: str | None,
) -> Iterator[list[sqlite3.Row]]:
    rows = conn.execute(
        "SELECT f.code, f.kind, b.score AS bm25 FROM fields f "
        "JOIN query_bm25 b ON b.id = f.id "
        "WHERE f.build_id = ? AND (? IS NULL OR f.code IN "
        "(SELECT code FROM concepts WHERE build_id = ? AND status = ?))",
        (build, status, build, status),
    )
    while chunk := rows.fetchmany(CHUNK_SIZE):
        yield chunk


def _vector_blob(row: sqlite3.Row, width: int, path: str) -> bytes:
    if (
        not isinstance(row[1], bytes)
        or not isinstance(row[2], bytes)
        or len(row[2]) != len(row[1]) * width * 4
    ):
        raise IndexStorageError(f"Stored vector length does not match the manifest ({path})")
    return row[2]


def _cosines(np: Any, rows: list[sqlite3.Row], query: Any, path: str) -> Any:
    width = len(query)
    blobs = [_vector_blob(row, width, path) for row in rows]
    matrix = np.frombuffer(b"".join(blobs), dtype="<f4").reshape(-1, width)
    if not np.isfinite(matrix).all():
        raise IndexStorageError(f"Stored vectors contain non-finite values ({path})")
    scores = np.zeros(len(matrix), dtype=np.float32)
    for start in range(0, len(matrix), CHUNK_SIZE):
        chunk = matrix[start : start + CHUNK_SIZE]
        norms = np.linalg.norm(chunk, axis=1)
        np.divide(chunk @ query, norms, out=scores[start : start + len(chunk)], where=norms != 0)
    return scores


def _query(np: Any, values: list[float]) -> Any:
    query = np.asarray(values, dtype="<f4")
    if not np.isfinite(query).all():
        raise IndexCompatibilityError("The query embedding contains non-finite values")
    norm = np.linalg.norm(query)
    return query / norm if norm else query


def _normal(value: float, limits: tuple[float, float]) -> float:
    low, high = limits
    return (value - low) / (high - low) if high != low else 1.0


def _exact(conn: sqlite3.Connection, build: str, query: str, status: str | None) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT code FROM concepts WHERE build_id = ? AND name_key = ? "
            "AND (? IS NULL OR status = ?)",
            (build, name_key(query), status, status),
        )
    }


def _scan(
    conn: sqlite3.Connection,
    manifest: IndexManifest,
    status: str | None,
    np: Any,
    query: Any,
    codes: dict[str, int],
) -> tuple[Any, Any, Any, Any]:
    count = conn.execute(
        "SELECT coalesce(sum(length(kinds)), 0) FROM concept_vectors WHERE build_id = ?",
        (manifest.build_id,),
    ).fetchone()[0]
    cosines = np.empty(count, dtype=np.float32)
    concepts = np.empty(count, dtype=np.int32)
    kinds = np.empty(count, dtype=np.int8)
    bm25 = np.zeros(count, dtype=np.float64)
    offset = 0
    starts = {}
    cursor = conn.execute(
        "SELECT code, kinds, vector FROM concept_vectors WHERE build_id = ? "
        "AND (? IS NULL OR code IN (SELECT code FROM concepts WHERE build_id = ? AND status = ?))",
        (manifest.build_id, status, manifest.build_id, status),
    )
    while rows := cursor.fetchmany(256):
        lengths = np.fromiter((len(row[1]) for row in rows), dtype=np.int32)
        end = offset + int(lengths.sum())
        cosines[offset:end] = _cosines(np, rows, query, manifest.index_path)
        concepts[offset:end] = np.repeat(
            np.fromiter((codes[row[0]] for row in rows), dtype=np.int32), lengths
        )
        kinds[offset:end] = np.frombuffer(b"".join(row[1] for row in rows), dtype=np.int8)
        positions = np.cumsum(lengths) - lengths + offset
        starts.update((row[0], int(start)) for row, start in zip(rows, positions, strict=True))
        offset = end
    _scatter_bm25(conn, bm25, starts)
    return cosines[:offset], concepts[:offset], kinds[:offset], bm25[:offset]


def _scatter_bm25(conn: sqlite3.Connection, bm25: Any, starts: dict[str, int]) -> None:
    for row in conn.execute(
        "SELECT f.code, f.position, b.score FROM query_bm25 b JOIN fields f ON f.id = b.id"
    ):
        if row[0] in starts:
            bm25[starts[row[0]] + row[1]] = row[2]


def _normal_array(np: Any, values: Any, limits: tuple[float, float]) -> Any:
    low, high = limits
    if low == high:
        return np.ones(len(values), dtype=np.float64)
    return (values.astype(np.float64) - low) / (high - low)


def _winners(np: Any, scores: Any, concepts: Any, kinds: Any, count: int) -> tuple[Any, Any]:
    best = np.full(count, -np.inf)
    np.maximum.at(best, concepts, scores)
    matches = scores == best[concepts]
    best_kind = np.full(count, len(_PRIORITY), dtype=np.int8)
    np.minimum.at(best_kind, concepts[matches], kinds[matches])
    matches &= kinds == best_kind[concepts]
    winning = np.full(count, len(scores), dtype=np.int64)
    np.minimum.at(winning, concepts[matches], np.flatnonzero(matches))
    return best, winning


def _page_indices(np: Any, scores: Any, exact: Any, offset: int, limit: int) -> Any:
    count = min(offset + limit, len(scores))
    if not count:
        return np.empty(0, dtype=np.int64)
    partition = np.argpartition(scores, len(scores) - count)[-count:]
    threshold = scores[partition].min()
    above = np.flatnonzero(scores > threshold)
    # argpartition alone picks arbitrary equal scores. Resolve the entire boundary
    # tie using the already lexicographically ordered concept indices.
    tied = scores == threshold
    boundary = np.concatenate((np.flatnonzero(tied & exact), np.flatnonzero(tied & ~exact)))
    selected = np.concatenate((above, boundary[: count - len(above)]))
    order = np.lexsort((selected, ~exact[selected], -scores[selected]))
    return selected[order][offset:]


def _vector_page(
    conn: sqlite3.Connection,
    manifest: IndexManifest,
    values: list[float],
    mode: str,
    offset: int,
    limit: int,
    status: str | None,
    bm25_limits: tuple[float, float],
    exact: set[str],
) -> tuple[list[tuple[str, FieldScore]], int]:
    np = _numpy()
    vector = _query(np, values)
    codes = [
        row[0]
        for row in conn.execute(
            "SELECT code FROM concepts WHERE build_id = ? "
            "AND (? IS NULL OR status = ?) ORDER BY code",
            (manifest.build_id, status, status),
        )
    ]
    raw, concepts, kinds, bm25 = _scan(
        conn,
        manifest,
        status,
        np,
        vector,
        {code: i for i, code in enumerate(codes)},
    )
    if not len(raw):
        return [], 0
    normalized = _normal_array(np, raw, (float(raw.min()), float(raw.max())))
    matched = bm25 != 0
    bm25[matched] = _normal_array(np, bm25[matched], bm25_limits)
    bm25_weight, vector_weight = _WEIGHTS[mode]
    scores = bm25_weight * bm25 + vector_weight * normalized
    best, winning = _winners(np, scores, concepts, kinds, len(codes))
    exact_mask = np.fromiter((code in exact for code in codes), dtype=np.bool_)
    best[exact_mask] = 1.0
    selected = _page_indices(np, best, exact_mask, offset, limit)
    names = tuple(_PRIORITY)
    results = []
    for index in selected:
        field = winning[index]
        score = FieldScore(
            float(best[index]), float(bm25[field]), float(normalized[field]), names[kinds[field]]
        )
        if exact_mask[index]:
            score = FieldScore(1.0, float(mode != "vector"), 1.0, "name")
        results.append((codes[index], score))
    return results, len(codes)


def _bm25_page(
    conn: sqlite3.Connection,
    manifest: IndexManifest,
    status: str | None,
    limits: tuple[float, float],
    exact: set[str],
    offset: int,
    limit: int,
) -> tuple[list[tuple[str, FieldScore]], int]:
    best: dict[str, FieldScore] = {}
    for rows in _bm25_rows(conn, manifest.build_id, status):
        for row in rows:
            score = _normal(row["bm25"], limits)
            candidate = FieldScore(score, score, 0.0, row["kind"])
            previous = best.get(row["code"], FieldScore(float("-inf"), 0.0, 0.0, "definition"))
            if (-score, _PRIORITY[candidate.kind]) < (
                -previous.score,
                _PRIORITY[previous.kind],
            ):
                best[row["code"]] = candidate
    best.update({code: FieldScore(1.0, 1.0, 0.0, "name") for code in exact})
    selected = heapq.nsmallest(
        offset + limit,
        (code for code in best),
        key=lambda code: (-best[code].score, code not in exact, code),
    )
    return [(code, best[code]) for code in selected[offset:]], len(best)


def rank_page(
    conn: sqlite3.Connection,
    manifest: IndexManifest,
    query: str,
    values: list[float] | None,
    mode: str,
    offset: int,
    limit: int,
    status: str | None,
) -> tuple[list[tuple[str, FieldScore]], int]:
    """Keep numeric field scores, never the full vector matrix or a sorted full ranking."""
    limits = _bm25(conn, manifest.build_id, query, mode)
    exact = _exact(conn, manifest.build_id, query, status)
    if values is None:
        return _bm25_page(conn, manifest, status, limits, exact, offset, limit)
    return _vector_page(conn, manifest, values, mode, offset, limit, status, limits, exact)
