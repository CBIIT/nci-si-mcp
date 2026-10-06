"""Dependencies shared by one server's handlers."""

from __future__ import annotations

from .cadsr import CaDSRClient
from .config import Settings
from .embeddings import EmbeddingProvider, create_embedding_provider
from .evs import LICENSE_KEY_HEADER, EVSClient
from .http_client import HttpClient
from .index import LocalIndex


class Context:
    def __init__(
        self,
        settings: Settings,
        *,
        evs: EVSClient | None = None,
        cadsr: CaDSRClient | None = None,
        index: LocalIndex | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.settings = settings
        self.cadsr = cadsr or CaDSRClient(settings)
        self.evs = evs or EVSClient(
            settings.evs_base_url,
            settings.timeout_seconds,
            max_attempts=settings.evs_max_attempts,
            retry_backoff_seconds=settings.evs_retry_backoff_seconds,
            max_response_bytes=settings.evs_max_response_bytes,
            license_key=settings.evs_license_key,
        )
        self.index = index or LocalIndex(settings.data_dir)
        self.fhir = HttpClient(
            settings.evs_fhir_base_url,
            label="EVS_FHIR",
            timeout_seconds=settings.timeout_seconds,
            max_attempts=settings.evs_max_attempts,
            retry_backoff_seconds=settings.evs_retry_backoff_seconds,
            max_response_bytes=settings.evs_max_response_bytes,
            size_bound="NCI_SI_EVS_MAX_RESPONSE_BYTES",
            credentials={LICENSE_KEY_HEADER: settings.evs_license_key}
            if settings.evs_license_key
            else None,
        )
        self.embedding_provider = embedding_provider or create_embedding_provider(
            settings.embedding_provider, settings.embedding_model
        )
