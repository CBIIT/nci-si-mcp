"""Application service used by the CLI and MCP entrypoint."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Iterable
from itertools import batched
from typing import Any, cast

from .cadsr import CadsrAdapter
from .config import Settings
from .embeddings import EmbeddingProvider, create_embedding_provider
from .errors import (
    ErrorCode,
    IndexCompatibilityError,
    IndexStorageError,
    InputValidationError,
    NoActiveIndexError,
    error_response,
)
from .evaluation import DEFAULT_GOLD_QUERIES, evaluate_retrieval
from .evs import (
    EVSClient,
    EVSError,
    EVSNotFoundError,
    EVSResponseError,
    EVSUnavailableError,
    ReleaseResolutionError,
    normalize_concept,
    verify_release,
)
from .index import LocalIndex
from .models import ReleaseInfo, utc_now_iso
from .traversal import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_EDGES,
    DEFAULT_MAX_NODES,
    select_edge_types,
    traverse_ncit,
)
from .validation import (
    validate_ncit_code,
    validate_ncit_codes,
    validate_search,
    validate_traversal,
)

logger = logging.getLogger(__name__)

# Expected failures and the error code each is reported under. An exception
# gets the code of its nearest listed class. Anything else is a bug and propagates.
_ERROR_CODES: dict[type[Exception], ErrorCode] = {
    InputValidationError: "invalid_request",
    EVSNotFoundError: "concept_not_found",
    ReleaseResolutionError: "release_unresolved",
    EVSResponseError: "evs_invalid_response",
    EVSError: "evs_unavailable",
    NoActiveIndexError: "no_active_index",
    IndexCompatibilityError: "index_incompatible",
    IndexStorageError: "index_storage_error",
}
_EXPECTED_ERRORS = tuple(_ERROR_CODES)


def _envelope(operation: str, exc: Exception) -> dict[str, Any]:
    code = next(_ERROR_CODES[cls] for cls in type(exc).__mro__ if cls in _ERROR_CODES)
    logger.warning("%s_failed error=%s message=%s", operation, code, exc)
    return error_response(code, str(exc))


def _enveloped[Method: Callable[..., dict[str, Any]]](method: Method) -> Method:
    """Report a method's expected failures as error envelopes instead of raising."""

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return method(*args, **kwargs)
        except _EXPECTED_ERRORS as exc:
            return _envelope(method.__name__, exc)

    return cast(Method, wrapper)


class NCISIService:
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
        )
        self.index = index or LocalIndex(settings.data_dir)
        self.embedding_provider = embedding_provider or create_embedding_provider(
            settings.embedding_provider, settings.embedding_model
        )
        self.cadsr = cadsr or CadsrAdapter()

    @_enveloped
    def release_info(self) -> dict[str, Any]:
        """Report EVS, release and index status; EVS failures are nested, not fatal."""

        def evs_status(fetch: Callable[[], dict[str, Any]]) -> dict[str, Any]:
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
                "active_index_compatible": bool(
                    manifest
                    and manifest.embedding_matches(
                        self.embedding_provider.name, self.embedding_provider.model
                    )
                ),
            },
            "retrieved_at": utc_now_iso(),
        }

    @_enveloped
    def index_manifest(self) -> dict[str, Any]:
        """Return the manifest of the local index under `active_index`, or null."""

        manifest = self.index.get_active_manifest()
        return {"active_index": manifest.to_dict() if manifest else None}

    def _fetch_for_index(
        self, codes: list[str], release: ReleaseInfo
    ) -> dict[str, dict[str, Any]]:
        """Fetch the payloads by code, pinned to the release. EVS omits unknown codes."""

        raw_concepts: list[dict[str, Any]] = []
        for batch in batched(codes, self.settings.index_batch_size, strict=False):
            raw_concepts.extend(
                self.evs.get_concepts_by_codes(batch, terminology=release.pinned_terminology)
            )
        verify_release(raw_concepts, release.version)
        concepts = {str(raw.get("code") or ""): raw for raw in raw_concepts}
        if not concepts.keys() <= set(codes):
            raise EVSResponseError("EVS returned a concept that was not requested")
        return concepts

    @_enveloped
    def index_codes(self, codes: Iterable[str]) -> dict[str, Any]:
        normalized_codes = validate_ncit_codes(codes)
        release = self.evs.resolve_monthly_ncit_release()
        concepts = self._fetch_for_index(normalized_codes, release)
        missing_codes = [code for code in normalized_codes if code not in concepts]
        if missing_codes:
            logger.warning("index_codes_failed error=concepts_missing codes=%s", missing_codes)
            return error_response(
                "concepts_missing",
                f"NCIt release {release.version} has no concept {', '.join(missing_codes)}; "
                "the index was not changed",
                missing_codes=missing_codes,
            )
        manifest = self.index.upsert_concepts(
            raw_concepts=concepts.values(),
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
    ) -> dict[str, Any]:
        query, limit, mode = validate_search(query, limit, mode)
        hits = self.index.search(query, self.embedding_provider, limit=limit, mode=mode)
        if hits:
            release_version: str | None = hits[0].concept.release_version
        else:
            manifest = self.index.get_active_manifest()
            release_version = manifest.release_version if manifest else None
        return {
            "query": query,
            "mode": mode,
            "release_version": release_version,
            "hits": [hit.to_dict(include_raw=include_raw) for hit in hits],
            "retrieved_at": utc_now_iso(),
        }

    @_enveloped
    def lookup(self, code: str, live_only: bool = False, include_raw: bool = False) -> dict[str, Any]:
        """Read one concept from live EVS, pinned to the current monthly release.

        Unless `live_only` is set, the result must agree with the active index:
        a different current release is a `version_mismatch`, and when EVS is
        unreachable the concept is served from the index, marked as a fallback.
        With `live_only` the index is not read.
        """

        code = validate_ncit_code(code)
        manifest = None if live_only else self.index.get_active_manifest()
        try:
            release = self.evs.resolve_monthly_ncit_release()
            if manifest and manifest.release_version != release.version:
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

        verify_release([raw], release.version)
        concept = normalize_concept(raw, release_date=release.date, source="live_evs")
        return concept.to_dict(include_raw=include_raw)

    @_enveloped
    def traverse(
        self,
        start_codes: list[str],
        direction: str = "out",
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_edges: int = DEFAULT_MAX_EDGES,
        include_hierarchy: bool = True,
        include_roles: bool = True,
        include_associations: bool = True,
        relationship_names: list[str] | None = None,
        edge_types: list[str] | None = None,
    ) -> dict[str, Any]:
        start_codes, direction, edge_types, relationship_names = validate_traversal(
            start_codes,
            direction,
            max_depth,
            max_nodes,
            max_edges,
            edge_types,
            relationship_names,
        )
        selected = select_edge_types(
            direction, include_hierarchy, include_roles, include_associations, edge_types
        )
        return traverse_ncit(
            self.evs,
            start_codes,
            self.evs.resolve_monthly_ncit_release(),
            selected,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_edges=max_edges,
            relationship_names=relationship_names,
        ).to_dict()

    @_enveloped
    def evaluate(self) -> dict[str, Any]:
        """Score BM25, vector and hybrid ranking on the built-in gold queries."""

        results = evaluate_retrieval(self.index, self.embedding_provider)
        gold_codes = {code for gold in DEFAULT_GOLD_QUERIES for code in gold.expected_codes}
        return {
            "results": [result.to_dict() for result in results],
            # A gold concept that is not indexed can never be found.
            "gold_codes_not_indexed": sorted(
                code for code in gold_codes if not self.index.get_concept(code)
            ),
        }

    def cadsr_status(self) -> dict[str, Any]:
        return self.cadsr.status().to_dict()
