"""Small retrieval evaluation helper for ranker/model comparisons."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List

from .embeddings import EmbeddingProvider
from .index import LocalIndex


@dataclass(frozen=True)
class GoldQuery:
    query: str
    expected_codes: List[str]


@dataclass(frozen=True)
class EvaluationResult:
    mode: str
    query_count: int
    hit_at_1: float
    hit_at_5: float
    mean_reciprocal_rank: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_GOLD_QUERIES = [
    GoldQuery("kinase inhibition", ["C40704", "C116938"]),
    GoldQuery("tumor", ["C3262"]),
    GoldQuery("neoplasm", ["C3262"]),
]


def evaluate_retrieval(
    index: LocalIndex,
    embedding_provider: EmbeddingProvider,
    gold_queries: Iterable[GoldQuery] = DEFAULT_GOLD_QUERIES,
    modes: Iterable[str] = ("bm25", "vector", "hybrid"),
    limit: int = 10,
) -> List[EvaluationResult]:
    queries = list(gold_queries)
    results: List[EvaluationResult] = []
    for mode in modes:
        hit_1 = 0
        hit_5 = 0
        reciprocal_ranks: List[float] = []
        for gold in queries:
            hits = index.search(gold.query, embedding_provider, limit=limit, mode=mode)
            ranked_codes = [hit.concept.code for hit in hits]
            expected = set(gold.expected_codes)
            if ranked_codes[:1] and ranked_codes[0] in expected:
                hit_1 += 1
            if expected.intersection(ranked_codes[:5]):
                hit_5 += 1
            reciprocal_rank = 0.0
            for idx, code in enumerate(ranked_codes, start=1):
                if code in expected:
                    reciprocal_rank = 1.0 / idx
                    break
            reciprocal_ranks.append(reciprocal_rank)
        denominator = len(queries) or 1
        results.append(
            EvaluationResult(
                mode=mode,
                query_count=len(queries),
                hit_at_1=hit_1 / denominator,
                hit_at_5=hit_5 / denominator,
                mean_reciprocal_rank=sum(reciprocal_ranks) / denominator,
            )
        )
    return results
