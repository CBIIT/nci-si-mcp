"""NCI EVS REST client and normalization logic."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import NcitConcept, ReleaseInfo, utc_now_iso


class EVSError(RuntimeError):
    """Base error for EVS client failures."""


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


class EVSClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        query = ""
        if params:
            filtered = {key: value for key, value in params.items() if value is not None}
            query = "?" + urlencode(filtered, doseq=True) if filtered else ""
        url = f"{self.base_url}{path}{query}"
        request = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - exercised through callers with fakes
            raise EVSError(f"EVS request failed for {path}: {exc}") from exc

    def get_api_version(self) -> Dict[str, Any]:
        return self._get_json("/api/v1/version")

    def get_terminologies(self) -> List[Dict[str, Any]]:
        data = self._get_json("/api/v1/metadata/terminologies")
        if not isinstance(data, list):
            raise EVSError("EVS terminology metadata response was not a list")
        return data

    def resolve_monthly_ncit_release(self) -> ReleaseInfo:
        return select_monthly_ncit_release(self.get_terminologies())

    def get_codes(self, terminology: str = "ncit") -> List[str]:
        data = self._get_json(f"/api/v1/concept/{terminology}/codes")
        if not isinstance(data, list):
            raise EVSError("EVS code response was not a list")
        return [str(code) for code in data]

    def get_concepts_by_codes(
        self,
        codes: Iterable[str],
        terminology: str = "ncit",
        include: str = "summary,definitions,synonyms,properties",
    ) -> List[Dict[str, Any]]:
        code_list = [code.strip() for code in codes if code and code.strip()]
        if not code_list:
            return []
        data = self._get_json(
            f"/api/v1/concept/{terminology}",
            {"list": ",".join(code_list), "include": include},
        )
        if not isinstance(data, list):
            raise EVSError("EVS concept list response was not a list")
        return data

    def get_concept(
        self,
        code: str,
        terminology: str = "ncit",
        include: str = "summary,definitions,synonyms,properties,parents,children,roles,inverseRoles,associations,inverseAssociations",
    ) -> Dict[str, Any]:
        return self._get_json(
            f"/api/v1/concept/{terminology}/{code}",
            {"include": include},
        )

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

    def get_related(self, code: str, relation: str, terminology: str = "ncit") -> List[Dict[str, Any]]:
        data = self._get_json(f"/api/v1/concept/{terminology}/{code}/{relation}")
        if isinstance(data, dict):
            for key in ("concepts", "roles", "associations", "associationEntries"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
            return [data]
        if isinstance(data, list):
            return data
        raise EVSError(f"EVS relation response for {relation} was not a list or object")
