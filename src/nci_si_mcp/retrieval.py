"""Pure-Python retrieval utilities for the local MVP index."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, Iterable, List, Sequence, Tuple


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> List[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return float(sum(a * b for a, b in zip(left, right)))


class BM25Index:
    def __init__(self, documents: Iterable[Tuple[str, str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.documents: Dict[str, List[str]] = {
            doc_id: tokenize(text) for doc_id, text in documents
        }
        self.doc_count = len(self.documents)
        self.avg_doc_len = (
            sum(len(tokens) for tokens in self.documents.values()) / self.doc_count
            if self.doc_count
            else 0.0
        )
        self.doc_freq: Counter[str] = Counter()
        for tokens in self.documents.values():
            self.doc_freq.update(set(tokens))

    def scores(self, query: str) -> Dict[str, float]:
        query_terms = tokenize(query)
        if not query_terms or not self.documents:
            return {}
        scores: Dict[str, float] = {}
        for doc_id, tokens in self.documents.items():
            term_freq = Counter(tokens)
            score = 0.0
            doc_len = len(tokens)
            for term in query_terms:
                if term not in term_freq:
                    continue
                df = self.doc_freq.get(term, 0)
                idf = math.log(1 + (self.doc_count - df + 0.5) / (df + 0.5))
                numerator = term_freq[term] * (self.k1 + 1)
                denominator = term_freq[term] + self.k1 * (
                    1 - self.b + self.b * doc_len / (self.avg_doc_len or 1)
                )
                score += idf * numerator / denominator
            if score:
                scores[doc_id] = score
        return scores


def min_max_normalize(scores: Dict[str, float]) -> Dict[str, float]:
    if not scores:
        return {}
    values = list(scores.values())
    low = min(values)
    high = max(values)
    if high == low:
        return {key: 1.0 for key in scores}
    return {key: (value - low) / (high - low) for key, value in scores.items()}
