"""Application service used by the CLI and MCP entrypoint."""

from __future__ import annotations

import functools
import logging
import sqlite3
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Type, TypeVar, cast

from .cadsr import CadsrAdapter
from .config import Settings
from .embeddings import EmbeddingProvider, create_embedding_provider
from .errors import (
    ErrorCode,
    IndexBuildError,
    IndexCompatibilityError,
    InputValidationError,
    NoActiveIndexError,
    error_response,
)
from .evaluation import evaluate_retrieval
from .evs import (
    EVSClient,
    EVSError,
    EVSNotFoundError,
    EVSResponseError,
    EVSUnavailableError,
    ReleaseResolutionError,
    normalize_concept,
)
from .index import LocalIndex
from .models import utc_now_iso
from .traversal import DEFAULT_MAX_DEPTH, DEFAULT_MAX_EDGES, DEFAULT_MAX_NODES, traverse_ncit
from .validation import (
    validate_ncit_code,
    validate_ncit_codes,
    validate_search,
    validate_traversal,
)

logger = logging.getLogger(__name__)

# Expected failures and the error code each is reported under. Order matters:
# a subclass must come before its base. Anything else is a bug and propagates.
_ERROR_CODES: Tuple[Tuple[Type[Exception], ErrorCode], ...] = (
    (InputValidationError, "invalid_request"),
    (EVSNotFoundError, "concept_not_found"),
    (ReleaseResolutionError, "release_unresolved"),
    (EVSResponseError, "evs_invalid_response"),
    (EVSError, "evs_unavailable"),
    (NoActiveIndexError, "no_active_index"),
    (IndexCompatibilityError, "index_incompatible"),
    (IndexBuildError, "index_build_failed"),
    (sqlite3.Error, "index_storage_error"),
)
_EXPECTED_ERRORS = tuple(error_type for error_type, _ in _ERROR_CODES)

_Method = TypeVar("_Method", bound=Callable[..., Dict[str, Any]])


def _envelope(operation: str, exc: Exception) -> Dict[str, Any]:
    code = next(code for error_type, code in _ERROR_CODES if isinstance(exc, error_type))
    logger.warning("%s_failed error=%s message=%s", operation, code, exc)
    return error_response(code, str(exc))


def _enveloped(method: _Method) -> _Method:
    """Report a method's expected failures as error envelopes instead of raising."""

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        try:
            return method(*args, **kwargs)
        except _EXPECTED_ERRORS as exc:
            return _envelope(method.__name__, exc)

    return cast(_Method, wrapper)


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

    @_enveloped
    def release_info(self) -> Dict[str, Any]:
        """Report EVS, release and index status; EVS failures are nested, not fatal."""

        def evs_status(fetch: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
            try:
                return fetch()
            except EVSError as exc:
                return _envelope("release_info", exc)

        manifest = self.index.get_active_manifest()
        return {
            "evs_api": evs_status(self.evs.get_api_version),
            "selected_monthly_release": evs_status(
                lambda: self.evs.resolve_monthly_ncit_release().to_dict()
            ),
            "active_index": manifest.to_dict() if manifest else None,
            "embedding": {
                "provider": self.embedding_provider.name,
                "model": self.embedding_provider.model,
                "active_index_compatible": not manifest
                or manifest.embedding_matches(
                    self.embedding_provider.name, self.embedding_provider.model
                ),
            },
            "retrieved_at": utc_now_iso(),
        }

    @_enveloped
    def index_codes(self, codes: Iterable[str]) -> Dict[str, Any]:
        normalized_codes = validate_ncit_codes(codes)
        release = self.evs.resolve_monthly_ncit_release()
        raw_concepts: List[Dict[str, object]] = []
        for offset in range(0, len(normalized_codes), self.settings.index_batch_size):
            batch = normalized_codes[offset : offset + self.settings.index_batch_size]
            raw_concepts.extend(
                self.evs.get_concepts_by_codes(batch, terminology=release.pinned_terminology)
            )
        returned_codes = {str(raw.get("code") or "").strip().upper() for raw in raw_concepts}
        missing_codes = [code for code in normalized_codes if code not in returned_codes]
        if missing_codes:
            logger.warning("index_codes_failed error=concepts_missing codes=%s", missing_codes)
            return error_response(
                "concepts_missing",
                f"NCIt release {release.version} has no concept {', '.join(missing_codes)}; "
                "the index was not changed",
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

    @_enveloped
    def search(
        self,
        query: str,
        limit: int = 10,
        mode: str = "hybrid",
        include_raw: bool = False,
    ) -> Dict[str, Any]:
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

    @_enveloped
    def lookup(self, code: str, live_only: bool = False, include_raw: bool = False) -> Dict[str, Any]:
        """Read one concept from live EVS, pinned to the current monthly release.

        Unless `live_only` is set, the result must agree with the active index:
        a different current release is a `version_mismatch`, and when EVS is
        unreachable the concept is served from the index, marked as a fallback.
        """

        code = validate_ncit_code(code)
        manifest = self.index.get_active_manifest()
        try:
            release = self.evs.resolve_monthly_ncit_release()
            if manifest and not live_only and manifest.release_version != release.version:
                logger.warning(
                    "lookup_failed error=version_mismatch live=%s index=%s",
                    release.version,
                    manifest.release_version,
                )
                return error_response(
                    "version_mismatch",
                    f"The current monthly release is {release.version} but the active index "
                    f"holds {manifest.release_version}. Rebuild the index, or use live_only "
                    "to read live EVS without consulting it.",
                    live_release_version=release.version,
                    active_index_release_version=manifest.release_version,
                )
            raw = self.evs.get_concept(code, terminology=release.pinned_terminology)
        except EVSUnavailableError as exc:
            cached = None if live_only else self.index.get_concept(code)
            if not cached:
                raise
            logger.warning("lookup_cache_fallback code=%s reason=%s", code, exc)
            result = cached.to_dict(include_raw=include_raw)
            result["fallback"] = {"reason": "evs_unavailable", "message": str(exc)}
            return result

        concept = normalize_concept(raw, release_date=release.date, source="live_evs")
        if concept.release_version != release.version:
            raise EVSResponseError(
                f"EVS served release {concept.release_version} for a request pinned to "
                f"{release.version}"
            )
        return concept.to_dict(include_raw=include_raw)

    @_enveloped
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
        start_codes, direction, edge_types, relationship_names = validate_traversal(
            start_codes,
            direction,
            max_depth,
            max_nodes,
            max_edges,
            edge_types,
            relationship_names,
        )
        release = self.evs.resolve_monthly_ncit_release()
        return traverse_ncit(
            client=self.evs,
            start_codes=start_codes,
            release_version=release.version,
            terminology=release.pinned_terminology,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_edges=max_edges,
            include_hierarchy=include_hierarchy,
            include_roles=include_roles,
            include_associations=include_associations,
            relationship_names=relationship_names,
            edge_types=edge_types,
        ).to_dict()

    @_enveloped
    def evaluate(self) -> Dict[str, Any]:
        """Score BM25, vector and hybrid ranking on the built-in gold queries."""

        results = evaluate_retrieval(self.index, self.embedding_provider)
        return {"results": [result.to_dict() for result in results]}

    def cadsr_status(self) -> Dict[str, Any]:
        return self.cadsr.status().to_dict()
