"""NCI EVS REST client and normalization logic."""

from __future__ import annotations

from collections.abc import Iterable
from http import HTTPStatus
from typing import Any

from .http_client import (
    HttpClient,
    UpstreamError,
    UpstreamRejectedError,
    UpstreamTimeoutError,
    UpstreamTooLargeError,
    UpstreamUnavailableError,
)
from .models import NcitConcept, ReleaseInfo, utc_now_iso

SOURCE_VOCABULARIES = {"ncit": "NCI Thesaurus"}

# The header that carries the licence key. It goes to EVS and to no other host (A7.5).
LICENSE_KEY_HEADER = "X-EVSRESTAPI-License-Key"

# What a concept request asks EVS to include: enough to build the search text
# for indexing, and additionally every relation list for a lookup.
INDEX_INCLUDE = "summary,definitions,synonyms,properties"
LOOKUP_INCLUDE = (
    f"{INDEX_INCLUDE},parents,children,roles,inverseRoles,associations,inverseAssociations"
)


TERMINOLOGIES_PATH = "/api/v1/metadata/terminologies"


def concept_path(terminology: str, code: str = "") -> str:
    """The path of a concept in `terminology`, or of the terminology's concept list."""

    return f"/api/v1/concept/{terminology}" + (f"/{code}" if code else "")


class EVSError(RuntimeError):
    """Base error for EVS client failures; `details` is what the error record carries."""

    def __init__(self, message: str, /, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class EVSUnavailableError(EVSError):
    """EVS could not be reached, or kept failing after the bounded retries."""


class EVSTimeoutError(EVSUnavailableError):
    """EVS did not answer within the timeout on any attempt."""


class EVSNotFoundError(EVSError):
    """The requested concept does not exist in the release that was asked for."""


class EVSResponseError(EVSError):
    """EVS rejected the request or answered with something the client cannot use."""


class EVSResponseTooLargeError(EVSResponseError):
    """EVS answered with more bytes than the configured response limit."""


class EVSReleaseMismatchError(EVSResponseError):
    """EVS served content of another release than the one requested."""


class ReleaseResolutionError(EVSError):
    """Raised when monthly NCIt cannot be resolved exactly."""


def _object(data: Any, what: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise EVSResponseError(f"EVS {what} was not an object")
    return data


def _object_list(data: Any, what: str) -> list[dict[str, Any]]:
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise EVSResponseError(f"EVS {what} was not a list of objects")
    return data


def object_list(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Return the list of objects under `key` of an EVS payload; absent means empty."""

    return _object_list(payload.get(key) or [], f"field '{key}'")


def verify_release(concepts: Iterable[dict[str, Any]], release_version: str) -> None:
    """Fail unless every concept payload was served from the release that was requested."""

    other = {str(raw.get("version") or "unknown") for raw in concepts} - {release_version}
    if other:
        served = sorted(other)
        raise EVSReleaseMismatchError(
            f"EVS served release {', '.join(served)} for a request pinned to {release_version}",
            requested=release_version,
            served=served,
            source="evs",
        )


def _tags(raw: dict[str, Any]) -> dict[str, Any]:
    return _object(raw.get("tags") or {}, "field 'tags'")


def release_from_terminology(raw: dict[str, Any]) -> ReleaseInfo:
    tags = _tags(raw)
    return ReleaseInfo(
        terminology=str(raw.get("terminology", "")),
        version=str(raw.get("version", "")),
        date=raw.get("date"),
        name=str(raw.get("name", "")),
        terminology_version=raw.get("terminologyVersion"),
        latest=bool(raw.get("latest")),
        monthly=str(tags.get("monthly", "")).lower() == "true",
        weekly=str(tags.get("weekly", "")).lower() == "true",
    )


def _is_latest_monthly_ncit(item: dict[str, Any]) -> bool:
    return (
        str(item.get("terminology", "")).lower() == "ncit"
        and bool(item.get("latest"))
        and str(_tags(item).get("monthly", "")).lower() == "true"
    )


def select_monthly_ncit_release(terminologies: Iterable[dict[str, Any]]) -> ReleaseInfo:
    """Pick the one NCIt row that is both `latest` and tagged monthly, or refuse.

    EVS lists every release it serves and marks `latest` per channel, so the
    weekly and the monthly channel can each have a latest row.
    """

    candidates = [
        release_from_terminology(item) for item in terminologies if _is_latest_monthly_ncit(item)
    ]
    if len(candidates) != 1:
        versions = [candidate.version for candidate in candidates]
        raise ReleaseResolutionError(
            "Expected exactly one latest monthly NCIt release; "
            f"found {len(candidates)} ({versions}). Refusing to fall back to weekly.",
            requested="ncit monthly",
            source="evs",
        )
    if not candidates[0].version:
        raise ReleaseResolutionError(
            "The latest monthly NCIt release has no version", requested="ncit monthly", source="evs"
        )
    return candidates[0]


def _property_values(properties: list[dict[str, Any]], kind: str) -> list[Any]:
    return [
        item.get("value") for item in properties if item.get("type") == kind and item.get("value")
    ]


def _evidence(raw: dict[str, Any]) -> dict[str, Any]:
    """The definitions, synonyms and classifying properties of a concept payload."""

    properties = object_list(raw, "properties")
    return {
        "definitions": [
            {
                "definition": item.get("definition"),
                "type": item.get("type"),
                "source": item.get("source"),
            }
            for item in object_list(raw, "definitions")
            if item.get("definition")
        ],
        "synonyms": [
            {
                "name": item.get("name"),
                "term_type": item.get("termType"),
                "type": item.get("type"),
                "source": item.get("source"),
            }
            for item in object_list(raw, "synonyms")
            if item.get("name")
        ],
        "semantic_types": _property_values(properties, "Semantic_Type"),
        "contributing_sources": _property_values(properties, "Contributing_Source"),
    }


def normalize_concept(
    raw: dict[str, Any],
    release_date: str | None,
    source: str,
    retrieved_at: str | None = None,
) -> NcitConcept:
    terminology = str(raw.get("terminology") or "ncit")
    return NcitConcept(
        code=str(raw.get("code", "")),
        preferred_name=str(raw.get("name", "")),
        # Any other terminology keeps its identifier as the label.
        source_vocabulary=SOURCE_VOCABULARIES.get(terminology, terminology),
        terminology=terminology,
        release_version=str(raw.get("version", "")),
        release_date=release_date,
        retrieved_at=retrieved_at or utc_now_iso(),
        source=source,
        evidence=_evidence(raw),
        raw=raw,
    )


# The EVS error of each failure of the HTTP client, nearest class first.
_EVS_ERRORS: dict[type[UpstreamError], type[EVSError]] = {
    UpstreamTimeoutError: EVSTimeoutError,
    UpstreamUnavailableError: EVSUnavailableError,
    UpstreamTooLargeError: EVSResponseTooLargeError,
    UpstreamRejectedError: EVSResponseError,
}


def _evs_error(exc: UpstreamError) -> EVSError:
    if exc.details.get("status") == HTTPStatus.NOT_FOUND:
        return EVSNotFoundError(str(exc), **exc.details)
    error = next(error for kind, error in _EVS_ERRORS.items() if isinstance(exc, kind))
    return error(str(exc), **exc.details)


class EVSClient:
    """Read-only EVS REST client.

    Concept methods take a `terminology` path segment. Passing a release's
    `pinned_terminology` (for example `ncit_26.06e`) pins the request to that
    release; plain `ncit` lets EVS choose.
    """

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        *,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.25,
        max_response_bytes: int = 10 * 1024 * 1024,
        license_key: str | None = None,
    ) -> None:
        self.http = HttpClient(
            base_url,
            label="EVS",
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
            max_response_bytes=max_response_bytes,
            size_bound="NCI_SI_EVS_MAX_RESPONSE_BYTES",
            credentials={LICENSE_KEY_HEADER: license_key} if license_key else None,
        )

    @property
    def max_response_bytes(self) -> int:
        return self.http.max_response_bytes

    def uri(self, path: str) -> str:
        """The URL of `path`: what a request for it asks, for an item's provenance."""

        return f"{self.http.base_url}{path}"

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a JSON document; HTTP 404 raises EVSNotFoundError.

        That means "no such concept" only for a single-concept request; the other methods go
        through `_get_existing`.
        """

        try:
            return self.http.get_json(path, params)
        except UpstreamError as exc:
            raise _evs_error(exc) from exc

    def _get_existing(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a document that must exist, so a 404 means a wrong endpoint or release."""

        try:
            return self._get_json(path, params)
        except EVSNotFoundError as exc:
            raise EVSResponseError(
                f"{exc}; EVS does not serve this endpoint or release, check NCI_SI_EVS_BASE_URL",
                **exc.details,
            ) from exc

    def get_api_version(self) -> dict[str, Any]:
        return _object(self._get_existing("/api/v1/version"), "version response")

    def get_terminologies(self) -> list[dict[str, Any]]:
        return _object_list(self._get_existing(TERMINOLOGIES_PATH), "terminology metadata")

    def resolve_monthly_ncit_release(self) -> ReleaseInfo:
        return select_monthly_ncit_release(self.get_terminologies())

    def get_concepts_by_codes(
        self,
        codes: Iterable[str],
        terminology: str = "ncit",
        include: str = INDEX_INCLUDE,
    ) -> list[dict[str, Any]]:
        """Fetch several concepts in one request; EVS omits codes it does not know."""

        code_list = [code.strip() for code in codes if code and code.strip()]
        if not code_list:
            return []
        data = self._get_existing(
            concept_path(terminology),
            {"list": ",".join(code_list), "include": include},
        )
        return _object_list(data, "concept list response")

    def get_concept(
        self,
        code: str,
        terminology: str = "ncit",
        include: str = LOOKUP_INCLUDE,
    ) -> dict[str, Any]:
        try:
            data = self._get_json(concept_path(terminology, code), {"include": include})
        except EVSNotFoundError as exc:
            raise EVSNotFoundError(str(exc), identifiers=[code]) from exc
        return _object(data, "concept response")

    def get_descendants(
        self, code: str, max_level: int, terminology: str = "ncit"
    ) -> list[dict[str, Any]]:
        """Fetch the descendants EVS places within `max_level` levels, each with its `level`.

        The concept must be known to exist: a 404 here is not reported as a
        missing concept.
        """

        data = self._get_existing(
            f"{concept_path(terminology, code)}/descendants", {"maxLevel": max_level}
        )
        return _object_list(data, "descendants response")
