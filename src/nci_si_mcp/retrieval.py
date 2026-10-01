"""Pure-Python vector and score utilities for the local index."""

from __future__ import annotations

import re
from typing import Dict, List, Sequence

# Unicode-aware so that non-ASCII query terms reach FTS5, whose unicode61
# tokenizer folds case and diacritics on both sides of the match.
TOKEN_RE = re.compile(r"\w+")


def tokenize(text: str) -> List[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Dot product of two vectors; equals cosine similarity for unit vectors.

    Both shipped embedding providers return unit vectors.
    """

    if not left or not right or len(left) != len(right):
        return 0.0
    return float(sum(a * b for a, b in zip(left, right)))


def min_max_normalize(scores: Dict[str, float]) -> Dict[str, float]:
    if not scores:
        return {}
    values = list(scores.values())
    low = min(values)
    high = max(values)
    if high == low:
        return {key: 1.0 for key in scores}
    return {key: (value - low) / (high - low) for key, value in scores.items()}
