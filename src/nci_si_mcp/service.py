"""Application service used by the CLI and MCP entrypoint."""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from .cadsr import CadsrAdapter
from .config import Settings
from .embeddings import EmbeddingProvider, create_embedding_provider
from .errors import IndexCompatibilityError, InputValidationError, error_response
from .evs import EVSClient, EVSError, normalize_concept
from .index import LocalIndex
from .models import utc_now_iso
from .traversal import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_EDGES,
    DEFAULT_MAX_NODES,
    HARD_MAX_NODES,
    traverse_ncit,
)
from .validation import (
    validate_ncit_code,
    validate_ncit_codes,
    validate_search,
    validate_traversal,
)

logger = logging.getLogger(__name__)


class NCISIService:
    def __init__(
        self,
        settings: Settings,
        *,
        evs: Optional[EVSClient] = None,
        index: Optional[LocalIndex] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
        cadsr: Optional[CadsrAdapter] = None,
    ) -> None:
        self.settings = settings
        self.evs = evs or EVSClient(
            settings.evs_base_url,
            settings.timeout_seconds,
            max_attempts=settings.evs_max_attempts,
            retry_backoff_seconds=settings.evs_retry_backoff_seconds,
            max_response_bytes=settings.evs_max_response_bytes,
        )
        self.index = index or LocalIndex(settings.data_dir)
        self.embedding_provider = embedding_provider or create_embedding_provider(
            settings.embedding_provider, settings.embedding_model
        )
        self.cadsr = cadsr or CadsrAdapter()

    def release_info(self) -> Dict[str, Any]:
        try:
            evs_version = self.evs.get_api_version()
        except EVSError as exc:
            evs_version = error_response("evs_unavailable", str(exc))
        try:
            selected = self.evs.resolve_monthly_ncit_release().to_dict()
        except EVSError as exc:
            selected = error_response("release_unavailable", str(exc))
        manifest = self.index.get_active_manifest()
        embedding_compatible = bool(
            not manifest
            or (
                manifest.embedding_provider == self.embedding_provider.name
                and manifest.embedding_model == self.embedding_provider.model
            )
        )
        return {
            "evs_api": evs_version,
            "selected_monthly_release": selected,
            "active_index": manifest.to_dict() if manifest else None,
            "embedding": {
                "provider": self.embedding_provider.name,
                "model": self.embedding_provider.model,
                "active_index_compatible": embedding_compatible,
            },
            "retrieved_at": utc_now_iso(),
        }

    def index_codes(self, codes: Iterable[str]) -> Dict[str, Any]:
        try:
            normalized_codes = validate_ncit_codes(codes)
            release = self.evs.resolve_monthly_ncit_release()
            raw_concepts: List[Dict[str, object]] = []
            for offset in range(0, len(normalized_codes), self.settings.index_batch_size):
                batch = normalized_codes[offset : offset + self.settings.index_batch_size]
                raw_concepts.extend(self.evs.get_concepts_by_codes(batch))
            returned_codes = {
                str(raw.get("code") or "").strip().upper() for raw in raw_concepts
            }
            missing_codes = [code for code in normalized_codes if code not in returned_codes]
            if missing_codes:
                return error_response(
                    "concepts_missing",
                    "EVS did not return every requested concept; the index was not changed",
                    missing_codes=missing_codes,
                )
            manifest = self.index.upsert_concepts(
                raw_concepts=raw_concepts,
                release_date=release.date,
                embedding_provider=self.embedding_provider,
                expected_release_version=release.version,
            )
            logger.info(
                "index_build_complete release=%s concepts=%s provider=%s model=%s",
                manifest.release_version,
                manifest.concept_count,
                manifest.embedding_provider,
                manifest.embedding_model,
            )
            return manifest.to_dict()
        except InputValidationError as exc:
            return error_response("invalid_request", str(exc))
        except EVSError as exc:
            return error_response("evs_unavailable", str(exc))
        except IndexCompatibilityError as exc:
            return error_response("index_incompatible", str(exc))
        except (RuntimeError, ValueError) as exc:
            return error_response("index_build_failed", str(exc))

    def search(
        self,
        query: str,
        limit: int = 10,
        mode: str = "hybrid",
        include_raw: bool = False,
    ) -> Dict[str, Any]:
        try:
            query, limit, mode = validate_search(query, limit, mode)
            hits = self.index.search(query, self.embedding_provider, limit=limit, mode=mode)
            manifest = self.index.get_active_manifest()
            return {
                "query": query,
                "mode": mode,
                "release_version": manifest.release_version if manifest else None,
                "hits": [hit.to_dict(include_raw=include_raw) for hit in hits],
                "retrieved_at": utc_now_iso(),
            }
        except InputValidationError as exc:
            return error_response("invalid_request", str(exc))
        except IndexCompatibilityError as exc:
            return error_response("index_incompatible", str(exc))
        except (RuntimeError, ValueError) as exc:
            return error_response("search_unavailable", str(exc))

    def lookup(self, code: str, live_only: bool = False, include_raw: bool = False) -> Dict[str, Any]:
        try:
            code = validate_ncit_code(code)
        except InputValidationError as exc:
            return error_response("invalid_request", str(exc))
        manifest = self.index.get_active_manifest()
        try:
            release = self.evs.resolve_monthly_ncit_release()
        except EVSError as exc:
            if live_only:
                return error_response("evs_unavailable", str(exc))
            cached = self.index.get_concept(code, manifest.release_version if manifest else None)
            if cached:
                logger.warning("lookup_cache_fallback code=%s reason=release_metadata", code)
                return cached.to_dict(include_raw=include_raw)
            return error_response("evs_unavailable_no_active_cache", str(exc))

        try:
            raw = self.evs.get_concept(code)
            concept = normalize_concept(raw, release_date=release.date, source="live_evs")
            if manifest and concept.release_version != manifest.release_version and not live_only:
                return error_response(
                    "version_mismatch",
                    "Live EVS concept release does not match the active index. "
                    "Use live_only=true to inspect live data without cache mixing.",
                    live_release_version=concept.release_version,
                    active_index_release_version=manifest.release_version,
                )
            return concept.to_dict(include_raw=include_raw)
        except EVSError as exc:
            if live_only:
                return error_response("evs_unavailable", str(exc))
            cached = self.index.get_concept(code, manifest.release_version if manifest else None)
            if cached:
                logger.warning("lookup_cache_fallback code=%s reason=concept_lookup", code)
                return cached.to_dict(include_raw=include_raw)
            return error_response("evs_unavailable_no_active_cache", str(exc))

    def traverse(
        self,
        start_codes: List[str],
        direction: str = "out",
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_edges: int = DEFAULT_MAX_EDGES,
        include_hierarchy: bool = True,
        include_roles: bool = True,
        include_associations: bool = True,
        relationship_names: Optional[List[str]] = None,
        edge_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        try:
            start_codes, direction, edge_types = validate_traversal(
                start_codes,
                direction,
                max_depth,
                max_nodes,
                max_edges,
                edge_types,
            )
            if len(start_codes) > min(max_nodes, HARD_MAX_NODES):
                raise InputValidationError("max_nodes must accommodate every start code")
            release = self.evs.resolve_monthly_ncit_release()
            result = traverse_ncit(
                client=self.evs,
                start_codes=start_codes,
                release_version=release.version,
                direction=direction,
                max_depth=max_depth,
                max_nodes=max_nodes,
                max_edges=max_edges,
                include_hierarchy=include_hierarchy,
                include_roles=include_roles,
                include_associations=include_associations,
                relationship_names=relationship_names,
                edge_types=edge_types,
            )
            return result.to_dict()
        except InputValidationError as exc:
            return error_response("invalid_request", str(exc))
        except EVSError as exc:
            return error_response("evs_unavailable", str(exc))

    def cadsr_status(self) -> Dict[str, Any]:
        return self.cadsr.status().to_dict()
