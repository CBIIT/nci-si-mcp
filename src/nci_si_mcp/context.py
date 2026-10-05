"""Dependencies shared by one server's handlers."""

from __future__ import annotations

from .cadsr import CadsrAdapter
from .config import Settings
from .embeddings import EmbeddingProvider, create_embedding_provider
from .evs import EVSClient
from .index import LocalIndex


class Context:
    def __init__(
        self,
        settings: Settings,
        *,
        evs: EVSClient | None = None,
        index: LocalIndex | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        cadsr: CadsrAdapter | None = None,
    ) -> None:
        self.settings = settings
        self.evs = evs or EVSClient(
            settings.evs_base_url,
            settings.timeout_seconds,
            max_attempts=settings.evs_max_attempts,
            retry_backoff_seconds=settings.evs_retry_backoff_seconds,
            max_response_bytes=settings.evs_max_response_bytes,
            license_key=settings.evs_license_key,
        )
        self.index = index or LocalIndex(settings.data_dir)
        self.embedding_provider = embedding_provider or create_embedding_provider(
            settings.embedding_provider, settings.embedding_model
        )
        self.cadsr = cadsr or CadsrAdapter()
