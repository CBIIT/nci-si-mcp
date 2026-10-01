"""NCI EVS REST client and normalization logic."""

from __future__ import annotations

import json
import logging
import time
from http.client import HTTPException
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import NcitConcept, ReleaseInfo, utc_now_iso

logger = logging.getLogger(__name__)


class EVSError(RuntimeError):
    """Base error for EVS client failures."""


class EVSUnavailableError(EVSError):
    """EVS could not be reached, or kept failing after the bounded retries."""


class EVSNotFoundError(EVSError):
    """EVS answered 404: the requested concept or release does not exist."""


class EVSResponseError(EVSError):
    """EVS rejected the request or answered with something the client cannot use."""


class EVSResponseTooLargeError(EVSResponseError):
    """EVS answered with more bytes than the configured response limit."""


class ReleaseResolutionError(EVSError):
    """Raised when monthly NCIt cannot be resolved exactly."""


def _source_vocabulary(terminology: str) -> str:
    if terminology.lower() == "ncit":
        return "NCI Thesaurus"
    if terminology.lower() == "ncim":
        return "NCI Metathesaurus"
    return terminology


def release_from_terminology(raw: Dict[str, Any]) -> ReleaseInfo:
    tags = raw.get("tags") or {}
    return ReleaseInfo(
        terminology=str(raw.get("terminology", "")),
        version=str(raw.get("version", "")),
        date=raw.get("date"),
        name=str(raw.get("name", "")),
        terminology_version=raw.get("terminologyVersion"),
        latest=bool(raw.get("latest")),
        monthly=str(tags.get("monthly", "")).lower() == "true",
        weekly=str(tags.get("weekly", "")).lower() == "true",
        raw=raw,
    )


def select_monthly_ncit_release(terminologies: Iterable[Dict[str, Any]]) -> ReleaseInfo:
    candidates = [
        release_from_terminology(item)
        for item in terminologies
        if str(item.get("terminology", "")).lower() == "ncit"
        and bool(item.get("latest"))
        and str((item.get("tags") or {}).get("monthly", "")).lower() == "true"
    ]
    if len(candidates) != 1:
        versions = [candidate.version for candidate in candidates]
        raise ReleaseResolutionError(
            "Expected exactly one latest monthly NCIt release; "
            f"found {len(candidates)} ({versions}). Refusing to fall back to weekly."
        )
    if not candidates[0].version:
        raise ReleaseResolutionError("The latest monthly NCIt release has no version")
    return candidates[0]


def normalize_concept(
    raw: Dict[str, Any],
    release_date: Optional[str],
    source: str,
    retrieved_at: Optional[str] = None,
) -> NcitConcept:
    terminology = str(raw.get("terminology") or "ncit")
    properties = raw.get("properties") or []
    definitions = raw.get("definitions") or []
    synonyms = raw.get("synonyms") or []

    semantic_types = [
        item.get("value")
        for item in properties
        if item.get("type") == "Semantic_Type" and item.get("value")
    ]
    contributing_sources = [
        item.get("value")
        for item in properties
        if item.get("type") == "Contributing_Source" and item.get("value")
    ]

    evidence = {
        "definitions": [
            {
                "definition": item.get("definition"),
                "type": item.get("type"),
                "source": item.get("source"),
            }
            for item in definitions
            if item.get("definition")
        ],
        "synonyms": [
            {
                "name": item.get("name"),
                "term_type": item.get("termType"),
                "type": item.get("type"),
                "source": item.get("source"),
            }
            for item in synonyms
            if item.get("name")
        ],
        "semantic_types": semantic_types,
        "contributing_sources": contributing_sources,
    }

    return NcitConcept(
        code=str(raw.get("code", "")),
        preferred_name=str(raw.get("name", "")),
        source_vocabulary=_source_vocabulary(terminology),
        terminology=terminology,
        release_version=str(raw.get("version", "")),
        release_date=release_date,
        retrieved_at=retrieved_at or utc_now_iso(),
        source=source,
        evidence=evidence,
        raw=raw,
    )


def _http_error_message(exc: HTTPError) -> str:
    """Describe an HTTP failure, including the reason EVS gives in its error body."""

    detail = ""
    if exc.fp is not None:
        try:
            body = json.loads(exc.read(4096).decode("utf-8"))
        except (OSError, ValueError, HTTPException):
            body = None
        if isinstance(body, dict):
            detail = str(body.get("message") or "")
    parts = [f"HTTP {exc.code}", str(exc.reason or ""), f"({detail})" if detail else ""]
    return " ".join(part for part in parts if part)


def _object(data: Any, what: str) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise EVSResponseError(f"EVS {what} response was not an object")
    return data


def _object_list(data: Any, what: str) -> List[Dict[str, Any]]:
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise EVSResponseError(f"EVS {what} response was not a list of objects")
    return data


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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.max_response_bytes = max_response_bytes

    def _retry(self, path: str, attempt: int, message: str) -> None:
        delay = self.retry_backoff_seconds * (2 ** (attempt - 1))
        logger.warning(
            "evs_request_retry path=%s attempt=%s max_attempts=%s delay_seconds=%.3f reason=%s",
            path,
            attempt,
            self.max_attempts,
            delay,
            message,
        )
        if delay:
            time.sleep(delay)

    def _read_response(self, response: Any, path: str) -> Any:
        too_large = (
            f"EVS response for {path} exceeded {self.max_response_bytes} bytes "
            "(NCI_SI_EVS_MAX_RESPONSE_BYTES)"
        )
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_length = int(content_length)
            except (TypeError, ValueError):
                declared_length = None
            if declared_length is not None and declared_length > self.max_response_bytes:
                raise EVSResponseTooLargeError(too_large)
        payload = response.read(self.max_response_bytes + 1)
        if len(payload) > self.max_response_bytes:
            raise EVSResponseTooLargeError(too_large)
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EVSResponseError(f"EVS returned invalid JSON for {path}: {exc}") from exc

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET a JSON document, retrying transport failures, HTTP 429 and HTTP 5xx."""

        query = ""
        if params:
            filtered = {key: value for key, value in params.items() if value is not None}
            query = "?" + urlencode(filtered, doseq=True) if filtered else ""
        request = Request(f"{self.base_url}{path}{query}", headers={"Accept": "application/json"})
        attempt = 0
        while True:
            attempt += 1
            failure: Exception
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    return self._read_response(response, path)
            except HTTPError as exc:
                message = f"EVS request failed for {path}: {_http_error_message(exc)}"
                exc.close()
                if exc.code == 404:
                    raise EVSNotFoundError(message) from exc
                if exc.code != 429 and exc.code < 500:
                    raise EVSResponseError(message) from exc
                failure = exc
            except (OSError, HTTPException) as exc:
                message = f"EVS request failed for {path}: {getattr(exc, 'reason', None) or exc}"
                failure = exc
            if attempt >= self.max_attempts:
                raise EVSUnavailableError(message) from failure
            self._retry(path, attempt, message)

    def get_api_version(self) -> Dict[str, Any]:
        return _object(self._get_json("/api/v1/version"), "version")

    def get_terminologies(self) -> List[Dict[str, Any]]:
        return _object_list(
            self._get_json("/api/v1/metadata/terminologies"), "terminology metadata"
        )

    def resolve_monthly_ncit_release(self) -> ReleaseInfo:
        return select_monthly_ncit_release(self.get_terminologies())

    def get_codes(self, terminology: str = "ncit") -> List[str]:
        data = self._get_json(f"/api/v1/concept/{terminology}/codes")
        if not isinstance(data, list):
            raise EVSResponseError("EVS code response was not a list")
        return [str(code) for code in data]

    def get_concepts_by_codes(
        self,
        codes: Iterable[str],
        terminology: str = "ncit",
        include: str = "summary,definitions,synonyms,properties",
    ) -> List[Dict[str, Any]]:
        """Fetch several concepts in one request; EVS omits codes it does not know."""

        code_list = [code.strip() for code in codes if code and code.strip()]
        if not code_list:
            return []
        data = self._get_json(
            f"/api/v1/concept/{terminology}",
            {"list": ",".join(code_list), "include": include},
        )
        return _object_list(data, "concept list")

    def get_concept(
        self,
        code: str,
        terminology: str = "ncit",
        include: str = "summary,definitions,synonyms,properties,parents,children,roles,inverseRoles,associations,inverseAssociations",
    ) -> Dict[str, Any]:
        data = self._get_json(f"/api/v1/concept/{terminology}/{code}", {"include": include})
        return _object(data, "concept")

    def get_descendants(
        self, code: str, max_level: int, terminology: str = "ncit"
    ) -> List[Dict[str, Any]]:
        """Fetch every descendant within `max_level` hierarchy levels, each with its `level`."""

        data = self._get_json(
            f"/api/v1/concept/{terminology}/{code}/descendants", {"maxLevel": max_level}
        )
        return _object_list(data, "descendants")

    def search(
        self,
        term: str,
        terminology: str = "ncit",
        match_type: str = "contains",
        page_size: int = 10,
        include: str = "minimal",
    ) -> Dict[str, Any]:
        return self._get_json(
            f"/api/v1/concept/{terminology}/search",
            {
                "term": term,
                "type": match_type,
                "pageSize": page_size,
                "include": include,
            },
        )
