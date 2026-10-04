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
    PlatformError,
    call_correlation_id,
    correlated,
    current_correlation_id,
    is_error_record,
    serialise,
    with_next_step,
)
from .evaluation import DEFAULT_GOLD_QUERIES, evaluate_retrieval
from .evs import (
    TERMINOLOGIES_PATH,
    EVSClient,
    EVSError,
    EVSNotFoundError,
    EVSReleaseMismatchError,
    EVSResponseError,
    EVSResponseTooLargeError,
    EVSTimeoutError,
    EVSUnavailableError,
    ReleaseResolutionError,
    concept_path,
    normalize_concept,
    verify_release,
)
from .index import LocalIndex
from .models import (
    IndexManifest,
    NcitConcept,
    ProvenanceEnvelope,
    ReleaseInfo,
    release_ref,
    utc_now_iso,
)
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

# Expected failures: each exception type, the error code it is reported under, and the
# caller's next step, which is appended to the exception's message. An exception gets the
# entry of its nearest listed class, and its `details` attribute, if any, goes into the
# record. Anything else is a bug and propagates. A `PlatformError` is reported as it is,
# with the message its raiser wrote.
_ERROR_CODES: dict[type[Exception], tuple[ErrorCode, str]] = {
    InputValidationError: ("invalid_request", "Correct the argument and call again."),
    EVSNotFoundError: (
        "not_found",
        "Check the code against the current monthly release, which `release-info` names.",
    ),
    ReleaseResolutionError: (
        "release_not_available",
        "Retry later: no release can be selected until EVS marks exactly one monthly NCIt "
        "release as latest.",
    ),
    EVSReleaseMismatchError: (
        "release_mismatch",
        "Retry later; no content of another release is used.",
    ),
    EVSResponseTooLargeError: (
        "bound_exceeded",
        "Raise NCI_SI_EVS_MAX_RESPONSE_BYTES, or ask for fewer concepts.",
    ),
    EVSResponseError: (
        "upstream_unavailable",
        "Retry later; if it persists, check NCI_SI_EVS_BASE_URL.",
    ),
    EVSTimeoutError: ("timeout", "Retry later, or raise NCI_SI_TIMEOUT_SECONDS."),
    EVSError: ("upstream_unavailable", "Retry later."),
    NoActiveIndexError: ("internal_error", "Build the index with `index-sample` first."),
    IndexCompatibilityError: (
        "internal_error",
        "Rebuild the index with `index-sample`, or use the embedding settings it was built with.",
    ),
    IndexStorageError: (
        "internal_error",
        "Check that the index file is readable and writable and that nothing else holds it.",
    ),
}
_EXPECTED_ERRORS = (*_ERROR_CODES, PlatformError)


def _platform_error(exc: Exception) -> PlatformError:
    if isinstance(exc, PlatformError):
        return exc
    code, next_step = next(_ERROR_CODES[cls] for cls in type(exc).__mro__ if cls in _ERROR_CODES)
    details = getattr(exc, "details", {})
    return PlatformError(code, with_next_step(str(exc), next_step), **details)


def _envelope(operation: str, exc: Exception) -> dict[str, Any]:
    error = _platform_error(exc)
    logger.warning("%s_failed error=%s message=%s", operation, error.code, error.message)
    return serialise(error)


def _enveloped[Method: Callable[..., dict[str, Any]]](method: Method) -> Method:
    """Report a method's expected failures as error envelopes instead of raising.

    A call made outside any adapter gets a correlation identifier of its own, so that the
    provenance of its items and its errors carry the same one.
    """

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        with correlated(current_correlation_id()):
            try:
                return method(*args, **kwargs)
            except _EXPECTED_ERRORS as exc:
                return _envelope(method.__name__, exc)

    return cast("Method", wrapper)


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
            license_key=settings.evs_license_key,
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
            except (EVSError, PlatformError) as exc:
                return _envelope("release_info", exc)

        manifest = self.index.get_active_manifest()
        selected = evs_status(lambda: self.evs.resolve_monthly_ncit_release().to_dict())
        report = {
            "evs_api": evs_status(self.evs.get_api_version),
            "selected_monthly_release": selected,
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
        }
        # The report is dated and attributed by the release it selected, so there is none to
        # name when EVS could not say.
        if is_error_record(selected):
            return report
        return report | {"provenance": self._release_provenance(selected).to_dict()}

    def _release_provenance(self, selected: dict[str, Any]) -> ProvenanceEnvelope:
        """The provenance of the release report: the selected release, read from EVS now."""

        return ProvenanceEnvelope(
            release=release_ref(selected["terminology"], selected["version"], selected["date"]),
            source="evs_rest",
            served_by="live",
            retrieved_at=utc_now_iso(),
            correlation_id=call_correlation_id(),
            source_uri=self.evs.uri(TERMINOLOGIES_PATH),
        )

    @_enveloped
    def index_manifest(self) -> dict[str, Any]:
        """Return the manifest of the local index under `active_index`, or null."""

        manifest = self.index.get_active_manifest()
        if not manifest:
            return {"active_index": None}
        return {
            "active_index": manifest.to_dict() | {"provenance": manifest.provenance().to_dict()}
        }

    def _fetch_for_index(
        self, codes: list[str], release: ReleaseInfo
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Fetch the payloads pinned to the release, and the codes EVS returned.

        EVS omits the codes it does not know.
        """

        raw_concepts: list[dict[str, Any]] = []
        for batch in batched(codes, self.settings.index_batch_size, strict=False):
            raw_concepts.extend(
                self.evs.get_concepts_by_codes(batch, terminology=release.pinned_terminology)
            )
        verify_release(raw_concepts, release.version)
        returned_codes = {str(raw.get("code") or "") for raw in raw_concepts}
        if not returned_codes <= set(codes):
            raise EVSResponseError("EVS returned a concept that was not requested")
        return raw_concepts, returned_codes

    @_enveloped
    def index_codes(self, codes: Iterable[str]) -> dict[str, Any]:
        normalized_codes = validate_ncit_codes(codes)
        release = self.evs.resolve_monthly_ncit_release()
        raw_concepts, returned_codes = self._fetch_for_index(normalized_codes, release)
        missing_codes = [code for code in normalized_codes if code not in returned_codes]
        if missing_codes:
            raise PlatformError(
                "not_found",
                f"NCIt release {release.version} has no concept {', '.join(missing_codes)}; "
                "the index was not changed. Remove the codes, or check them against that release.",
                identifiers=missing_codes,
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
    ) -> dict[str, Any]:
        query, limit, mode = validate_search(query, limit, mode)
        hits, truncation = self.index.search_with_truncation(
            query, self.embedding_provider, limit=limit, mode=mode
        )
        result: dict[str, Any] = {
            "query": query,
            "mode": mode,
            "hits": [
                hit.to_dict(self._indexed_concept_uri(hit.concept), include_raw=include_raw)
                for hit in hits
            ],
            "truncation": truncation.to_dict(),
        }
        # A result with no item has none to carry the provenance (M3.2).
        if not hits:
            result["provenance"] = self._active_manifest().provenance().to_dict()
        return result

    def _active_manifest(self) -> IndexManifest:
        manifest = self.index.get_active_manifest()
        if not manifest:
            raise NoActiveIndexError("No active NCIt index is available")
        return manifest

    def _concept_uri(self, code: str, pinned_terminology: str) -> str:
        """The URL of the concept in the release that the terminology segment pins."""

        return self.evs.uri(concept_path(pinned_terminology, code))

    def _indexed_concept_uri(self, concept: NcitConcept) -> str:
        """The URL the indexed concept was read from, in the form EVS names a release."""

        return self._concept_uri(concept.code, f"{concept.terminology}_{concept.release_version}")

    @_enveloped
    def lookup(
        self, code: str, live_only: bool = False, include_raw: bool = False
    ) -> dict[str, Any]:
        """Read one concept from live EVS, pinned to the current monthly release.

        Unless `live_only` is set, the result must agree with the active index:
        a different current release is a `release_mismatch`, and when EVS is
        unreachable the concept is served from the index, marked as a fallback.
        With `live_only` the index is not read.
        """

        code = validate_ncit_code(code)
        manifest = None if live_only else self.index.get_active_manifest()
        try:
            release = self.evs.resolve_monthly_ncit_release()
            if manifest and manifest.release_version != release.version:
                raise PlatformError(
                    "release_mismatch",
                    f"The current monthly release is {release.version} but the active index "
                    f"holds {manifest.release_version}. Rebuild the index with `index-sample`, "
                    "or use live_only to read live EVS without consulting it.",
                    requested=release.version,
                    served=[manifest.release_version],
                    source="index",
                )
            raw = self.evs.get_concept(code, terminology=release.pinned_terminology)
        except EVSUnavailableError as exc:
            cached = None if live_only else self.index.get_concept(code)
            if not cached:
                raise
            logger.warning("lookup_cache_fallback code=%s reason=%s", code, exc)
            result = cached.to_dict(self._indexed_concept_uri(cached), include_raw=include_raw)
            result["fallback"] = {"reason": "upstream_unavailable", "message": str(exc)}
            return result

        verify_release([raw], release.version)
        concept = normalize_concept(raw, release_date=release.date, source="live_evs")
        uri = self._concept_uri(concept.code, release.pinned_terminology)
        return concept.to_dict(uri, include_raw=include_raw)

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
