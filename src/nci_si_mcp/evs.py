"""NCI EVS REST client and normalization logic."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .http_client import (
    HttpClient,
    UpstreamRejectedError,
)
from .models import NcitConcept, utc_now_iso
from .validation import RelationshipKind

if TYPE_CHECKING:
    from .release import ReleaseContext

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


def catalogue_path(terminology: str, kind: RelationshipKind) -> str:
    return f"/api/v1/metadata/{quote(terminology, safe='')}/{kind}s"


def concept_path(terminology: str, code: str = "") -> str:
    """The path of a concept in `terminology`, or of the terminology's concept list."""

    return f"/api/v1/concept/{quote(terminology, safe='')}" + (
        f"/{quote(code, safe='')}" if code else ""
    )


class EVSError(RuntimeError):
    """Base error for EVS client failures; `details` is what the error record carries."""

    def __init__(self, message: str, /, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class EVSNotFoundError(EVSError):
    """The requested concept does not exist in the release that was asked for."""


class EVSResponseError(EVSError):
    """EVS returned unusable content or lacks an endpoint that must exist."""


class EVSReleaseMismatchError(EVSResponseError):
    """EVS served content of another release than the one requested."""


class EVSReleaseNotFoundError(EVSError):
    """EVS does not serve the release (terminology version) a request was pinned to."""


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


def _empty_terminologies(data: Any) -> str | None:
    return "EVS listed no terminologies" if data == [] else None


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


def verify_content(concepts: list[dict[str, Any]], release: ReleaseContext) -> None:
    """Check self-described concept content against the call's pinned identity."""

    verify_release(concepts, release.version)
    if any(raw.get("terminology") != release.terminology for raw in concepts):
        raise EVSResponseError("EVS returned content of another or missing terminology")


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


# What EVS says of a request pinned to a release it does not serve.
_UNKNOWN_TERMINOLOGY = re.compile(r"Terminology not found\s*=\s*([^\s)]+)")


class EVSClient:
    """Read-only EVS REST client.

    Every content method requires a ReleaseContext and addresses that release.
    Full concept payloads must describe the same terminology and version.
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

    def uri(self, path: str, params: dict[str, Any] | None = None) -> str:
        """The URL of `path`: what a request for it asks, for an item's provenance."""

        return self.http.url(path, params)

    def _get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        reject: Callable[[Any], str | None] | None = None,
    ) -> Any:
        """GET a JSON document; HTTP 404 raises EVSNotFoundError.

        That means "no such concept" only for a single-concept request; the other methods go
        through `_get_existing`.
        """

        try:
            return self.http.get_json(path, params, reject=reject)
        except UpstreamRejectedError as exc:
            if exc.details.get("status") != HTTPStatus.NOT_FOUND:
                raise
            unknown = _UNKNOWN_TERMINOLOGY.search(str(exc))
            if unknown:
                raise EVSReleaseNotFoundError(str(exc), requested=unknown[1], source="evs") from exc
            raise EVSNotFoundError(str(exc), **exc.details) from exc

    def _get_existing(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        reject: Callable[[Any], str | None] | None = None,
    ) -> Any:
        """GET a document that must exist, so a 404 means a wrong endpoint or release."""

        try:
            return self._get_json(path, params, reject=reject)
        except EVSNotFoundError as exc:
            raise EVSResponseError(
                f"{exc}; EVS does not serve this endpoint or release, check NCI_SI_EVS_BASE_URL",
                **exc.details,
            ) from exc

    def get_api_version(self) -> dict[str, Any]:
        return _object(self._get_existing("/api/v1/version"), "version response")

    def get_terminologies(
        self, terminology: str | None = None, *, latest: bool = False, tag: str | None = None
    ) -> list[dict[str, Any]]:
        """The terminology rows EVS lists; `latest` and `tag` select a channel's current release."""

        params = {"terminology": terminology, "latest": "true" if latest else None, "tag": tag}
        reject = None
        if terminology is None and not latest and tag is None:
            # Unfiltered EVS metadata must name at least one served terminology.
            reject = _empty_terminologies
        return _object_list(
            self._get_existing(TERMINOLOGIES_PATH, params, reject=reject),
            "terminology metadata",
        )

    def get_concepts_by_codes(
        self,
        codes: Iterable[str],
        release: ReleaseContext,
        include: str = INDEX_INCLUDE,
    ) -> list[dict[str, Any]]:
        """Fetch several concepts in one request; EVS omits codes it does not know."""

        code_list = list(codes)
        if not code_list:
            return []
        data = self._get_existing(
            concept_path(release.pinned_terminology),
            {"list": ",".join(code_list), "include": include},
        )
        concepts = _object_list(data, "concept list response")
        verify_content(concepts, release)
        return concepts

    def get_relationship_catalogue(
        self, release: ReleaseContext, kind: RelationshipKind
    ) -> list[dict[str, Any]]:
        """Read one release's roles or associations, verifying every row's identity."""

        rows = _object_list(
            self._get_existing(catalogue_path(release.pinned_terminology, kind)),
            f"{kind} catalogue",
        )
        verify_content(rows, release)
        return rows

    def get_concept(
        self,
        code: str,
        release: ReleaseContext,
        include: str = LOOKUP_INCLUDE,
    ) -> dict[str, Any]:
        try:
            data = self._get_json(
                concept_path(release.pinned_terminology, code), {"include": include}
            )
        except EVSNotFoundError as exc:
            raise EVSNotFoundError(str(exc), identifiers=[code]) from exc
        concept = _object(data, "concept response")
        verify_content([concept], release)
        return concept

    def get_descendants(
        self, code: str, max_level: int, release: ReleaseContext
    ) -> list[dict[str, Any]]:
        """Fetch the descendants EVS places within `max_level` levels, each with its `level`.

        The concept must be known to exist: a 404 here is not reported as a
        missing concept. Compact entries do not self-describe their release;
        the pinned path establishes it, and later full concept reads verify it.
        """

        data = self._get_existing(
            f"{concept_path(release.pinned_terminology, code)}/descendants",
            {"maxLevel": max_level},
        )
        return _object_list(data, "descendants response")
