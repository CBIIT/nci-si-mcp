"""Read operations used by tools, resources and readiness."""

from abc import abstractmethod
from typing import Protocol

from ncit_index.embeddings import EmbeddingProvider

from .models import IndexManifest, NcitConcept, SearchHit


class IndexPort(Protocol):
    """Snapshot reads, never operator writes or evaluation."""

    @abstractmethod
    def get_active_manifest(self) -> IndexManifest | None:
        """Return the active manifest, or None when no completed build is active."""

    @abstractmethod
    def verify_active(self, provider: EmbeddingProvider) -> IndexManifest:
        """Return a usable active manifest; raise on absence or incompatible embeddings/schema."""

    @abstractmethod
    def get_concept_snapshot(self, code: str) -> tuple[IndexManifest | None, NcitConcept | None]:
        """Read one snapshot; absent build means (None, None), absent code means no concept."""

    @abstractmethod
    def search_page(
        self,
        query: str,
        embedding_provider: EmbeddingProvider,
        limit: int = 10,
        mode: str = "hybrid",
        *,
        requested_release: str | None = None,
        offset: int = 0,
        build_id: str | None = None,
        retired_status: str | None = None,
    ) -> tuple[list[SearchHit], int, IndexManifest]:
        """Read page/count/manifest atomically; reject a changed build or mismatched release."""
