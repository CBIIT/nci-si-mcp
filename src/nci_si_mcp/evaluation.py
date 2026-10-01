"""Small retrieval evaluation helper for ranker/model comparisons."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from .embeddings import EmbeddingProvider
from .index import LocalIndex


@dataclass(frozen=True, slots=True)
class GoldQuery:
    query: str
    expected_codes: list[str]


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    mode: str
    query_count: int
    hit_at_1: float
    hit_at_5: float
    mean_reciprocal_rank: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_GOLD_QUERIES = [
    GoldQuery("kinase inhibition", ["C40704", "C116938"]),
    GoldQuery("tumor", ["C3262"]),
    GoldQuery("neoplasm", ["C3262"]),
]


def _reciprocal_rank(ranked_codes: list[str], expected: set[str]) -> float:
    """One over the rank of the first expected code, or zero when none was found."""

    for rank, code in enumerate(ranked_codes, start=1):
        if code in expected:
            return 1.0 / rank
    return 0.0


def _evaluate_mode(
    index: LocalIndex,
    embedding_provider: EmbeddingProvider,
    queries: list[GoldQuery],
    mode: str,
    limit: int,
) -> EvaluationResult:
    hit_1 = 0
    hit_5 = 0
    reciprocal_ranks: list[float] = []
    for gold in queries:
        hits = index.search(gold.query, embedding_provider, limit=limit, mode=mode)
        ranked_codes = [hit.concept.code for hit in hits]
        expected = set(gold.expected_codes)
        hit_1 += bool(expected.intersection(ranked_codes[:1]))
        hit_5 += bool(expected.intersection(ranked_codes[:5]))
        reciprocal_ranks.append(_reciprocal_rank(ranked_codes, expected))
    denominator = len(queries) or 1
    return EvaluationResult(
        mode=mode,
        query_count=len(queries),
        hit_at_1=hit_1 / denominator,
        hit_at_5=hit_5 / denominator,
        mean_reciprocal_rank=sum(reciprocal_ranks) / denominator,
    )


def evaluate_retrieval(
    index: LocalIndex,
    embedding_provider: EmbeddingProvider,
    gold_queries: Iterable[GoldQuery] = DEFAULT_GOLD_QUERIES,
    modes: Iterable[str] = ("bm25", "vector", "hybrid"),
    limit: int = 10,
) -> list[EvaluationResult]:
    queries = list(gold_queries)
    return [_evaluate_mode(index, embedding_provider, queries, mode, limit) for mode in modes]
