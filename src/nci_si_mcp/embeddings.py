"""Embedding provider abstraction.

The hashing provider is intentionally simple and deterministic so tests and
local smoke runs do not require model downloads. Production evaluation can use
SentenceTransformersProvider for SapBERT, MiniLM, or internal models.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from typing import Iterable, List


class EmbeddingProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def embed(self, texts: Iterable[str]) -> List[List[float]]:
        raise NotImplementedError


class HashingEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dimensions: int = 128) -> None:
        self.name = "hashing"
        self.model = f"hashing-{dimensions}"
        self.dimensions = dimensions

    def embed(self, texts: Iterable[str]) -> List[List[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> List[float]:
        vector = [0.0] * self.dimensions
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = -1.0 if digest[4] % 2 else 1.0
            vector[idx] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector


class SentenceTransformersProvider(EmbeddingProvider):
    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "sentence-transformers is not installed. Install nci-si-mcp[embeddings]."
            ) from exc
        self.name = "sentence-transformers"
        self.model = model_name
        self._model = SentenceTransformer(model_name)

    def embed(self, texts: Iterable[str]) -> List[List[float]]:
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [list(map(float, row)) for row in vectors]


def create_embedding_provider(provider: str, model: str) -> EmbeddingProvider:
    if provider == "hashing" or model == "hashing":
        return HashingEmbeddingProvider()
    if provider == "sentence-transformers":
        return SentenceTransformersProvider(model)
    raise ValueError(f"Unknown embedding provider: {provider}")
