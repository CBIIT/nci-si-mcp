"""Pure-Python vector and score utilities for the local index."""

from __future__ import annotations

import re
from collections.abc import Sequence

# Unicode-aware so that non-ASCII query terms reach FTS5, whose unicode61
# tokenizer folds case and diacritics on both sides of the match.
TOKEN_RE = re.compile(r"\w+")


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Dot product of two vectors of one length; the cosine similarity of unit vectors.

    Both shipped embedding providers return unit vectors.
    """

    return float(sum(a * b for a, b in zip(left, right, strict=True)))


def min_max_normalize(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    values = list(scores.values())
    low = min(values)
    high = max(values)
    if high == low:
        return dict.fromkeys(scores, 1.0)
    return {key: (value - low) / (high - low) for key, value in scores.items()}
