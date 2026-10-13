"""Read operations used by tools, resources and readiness."""

from typing import Protocol

from ncit_index.embeddings import EmbeddingProvider

from .models import IndexManifest, NcitConcept, SearchHit


class IndexPort(Protocol):
    """Snapshot reads, never operator writes or evaluation."""

    def get_active_manifest(self) -> IndexManifest | None: ...

    def verify_active(self, provider: EmbeddingProvider) -> IndexManifest: ...

    def get_concept_snapshot(
        self, code: str
    ) -> tuple[IndexManifest | None, NcitConcept | None]: ...

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
    ) -> tuple[list[SearchHit], int, IndexManifest]: ...
