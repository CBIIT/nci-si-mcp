"""Contract-bound caDSR matching, with no alternative request or synthetic match."""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any, NotRequired, TypedDict, get_args

from . import cadsr_content as records
from .caching import select_cache_hint
from .cadsr import CDE_MATCH, VM_MATCH
from .context import Context
from .errors import InputValidationError, PlatformError
from .permissions import require
from .validation import (
    MatchedItemType,
    MatchStrictness,
    bounded,
    validate_choice,
    validate_identifier,
)

_MAX_INPUTS = 10


class MatchEntity(TypedDict):
    name: str
    userTip: NotRequired[str]
    permissibleValues: NotRequired[list[str]]


class SchemeFilter(TypedDict):
    publicId: str
    version: str


class MatchFilters(TypedDict, total=False):
    context: str
    workflowStatus: str
    registrationStatus: str
    valueDomainType: str
    classificationScheme: SchemeFilter


def _text(value: Any, parameter: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputValidationError("Must be nonblank text", parameter)
    return value


def _list(value: Any, parameter: str) -> list[Any]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_INPUTS:
        raise InputValidationError("Give between 1 and 10 items", parameter)
    return value


def _entity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - set(MatchEntity.__annotations__):
        raise InputValidationError("Use name, userTip and permissibleValues only", "entities")
    result: dict[str, Any] = {"entity": _text(value.get("name"), "entities.name")}
    if "userTip" in value:
        result["entityUserTip"] = _text(value["userTip"], "entities.userTip")
    if "permissibleValues" in value:
        result["pvvmData"] = _permissible_values(value["permissibleValues"])
    return result


def _permissible_values(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        raise InputValidationError("Must be a list of text values", "entities.permissibleValues")
    return [{"name": _text(value, "entities.permissibleValues")} for value in values]


def _headers(filters: MatchFilters | None, limit: int) -> dict[str, str]:
    if filters is not None and not isinstance(filters, dict):
        raise InputValidationError("Must be an object", "filters")
    headers = {"matchLimit": str(limit)}
    for key, value in (filters or {}).items():
        validate_choice(key, tuple(MatchFilters.__annotations__), "filters")
        if key == "classificationScheme":
            headers.update(_scheme_headers(value))
        else:
            headers["contexts" if key == "context" else key] = _header_text(value, f"filters.{key}")
    return headers


def _header_text(value: Any, parameter: str) -> str:
    text = _text(value, parameter)
    # Header transport cannot preserve control characters or arbitrary Unicode.
    if not text.isascii() or not text.isprintable():
        raise InputValidationError("Use printable ASCII for this upstream header", parameter)
    return text


def _scheme_headers(value: Any) -> dict[str, str]:
    parameter = "filters.classificationScheme"
    if not isinstance(value, dict) or set(value) != {"publicId", "version"}:
        raise InputValidationError("Give both publicId and version", parameter)
    validate_identifier(value["publicId"], r"[1-9][0-9]*", parameter)
    validate_identifier(value["version"], r"[0-9]+([.][0-9]+)?", parameter)
    return {
        "classificationSchemePublicId": value["publicId"],
        "classificationSchemeVersion": value["version"],
    }


def _reject_unsupported(model: str | None, threshold: float | None) -> None:
    for key, value in (("modelVariant", model), ("similarityThreshold", threshold)):
        if value is not None:
            raise InputValidationError(
                "The published matching contract has no such parameter; see the upstream "
                "requirements package C-6. Remove it until caDSR implements it.",
                key,
            )


def _matching_release(context: Context, pin: str | None) -> dict[str, str]:
    release = records._pin(context, pin)
    if pin is not None:
        raise PlatformError(
            "capability_unavailable",
            "The matching APIs lack a registryRelease contract field (C-1 for matching, "
            "upstream requirements package #42). Omit the pin until caDSR adds it.",
            capability="pinned matching",
        )
    return release


def _result(matches: list[dict[str, Any]], provenance: dict[str, Any]) -> dict[str, Any]:
    select_cache_hint(resolution=False, computed=True)
    return {"matches": matches} | ({} if matches else {"provenance": provenance})


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        records._malformed(f"match {key}")
    return value


def _score(raw: dict[str, Any]) -> int | float:
    value = raw.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        records._malformed("match score")
    if isinstance(value, float) and not math.isfinite(value):
        records._malformed("match score")
    return value


def _cde_matches(
    raw: dict[str, Any], entity: str, limit: int, provenance: dict[str, Any]
) -> list[dict[str, Any]]:
    if raw.get("entity") != entity or "matches" not in raw:
        records._malformed("matched entity or matches")
    return [
        {
            "entity": entity,
            "dataElement": records._element(row, [], provenance),
            "score": _score(row),
            "rule": _required_text(row, "ruleDescription"),
            "matchedText": _required_text(row, "matchedText"),
        }
        for row in records._rows(raw, "matches")[:limit]
    ]


def match_data_elements(
    context: Context,
    entities: list[MatchEntity],
    matchLimit: int = 10,  # noqa: N803 - public specification spelling.
    modelVariant: str | None = None,  # noqa: N803 - public specification spelling.
    similarityThreshold: float | None = None,  # noqa: N803 - public specification spelling.
    filters: MatchFilters | None = None,
    registryRelease: str | None = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """Match 1-10 described entities to caDSR data elements in caller/platform order.

    matchLimit defaults to 10, at most 100 per entity. modelVariant and similarityThreshold
    are invalid_request (requirements package C-6). Filters are upstream headers;
    classificationScheme requires both publicId and version. One apiinput object is sent
    per entity; any failure fails the call, never a partial success. Matching uses the
    configured match timeout (45 seconds by default). Results are computed, 0/private.
    Registry pins fail closed: published pins cannot yet address matching (C-1).
    """
    _reject_unsupported(modelVariant, similarityThreshold)
    inputs = [_entity(value) for value in _list(entities, "entities")]
    size = bounded(matchLimit, 100, "matchLimit")
    headers = _headers(filters, size)
    release = _matching_release(context, registryRelease)
    groups, provenance = match_entities(context, inputs, headers, release, size)
    return _result([match for group in groups for match in group], provenance)


def match_entities(
    context: Context,
    inputs: list[dict[str, Any]],
    headers: dict[str, str],
    release: dict[str, str],
    size: int,
) -> tuple[list[list[dict[str, Any]]], dict[str, Any]]:
    """Read validated entities under the caller's registry state, retaining each group."""
    require("match_data_elements")
    provenance = records._provenance(context, release, CDE_MATCH)
    groups = []
    responses: dict[str, dict[str, Any]] = {}
    for entity in inputs:
        key = json.dumps(entity, sort_keys=True)
        if key not in responses:
            require("match_data_elements")
            responses[key] = context.cadsr.match_data_element(entity, headers)
        groups.append(_cde_matches(responses[key], entity["entity"], size, provenance))
    return groups, provenance


def _vm_headers(strictness: str, scope: list[str] | None) -> dict[str, str]:
    validate_choice(strictness, get_args(MatchStrictness), "strictness")
    headers = {"matchType": strictness.capitalize(), "function": "match"}
    if scope is not None:
        if not isinstance(scope, list) or not scope:
            raise InputValidationError("Give a nonempty list of codes", "terminologyScope")
        headers["evsTerminologyCodes"] = ",".join(_terminology(code) for code in scope)
    return headers


def _terminology(value: Any) -> str:
    text = _header_text(value, "terminologyScope")
    if "," in text:
        raise InputValidationError("Give each code as a separate list item", "terminologyScope")
    return text


def _vm_item(raw: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    if raw.get("itemType") not in get_args(MatchedItemType):
        records._malformed("match itemType")
    item = {"publicId": raw.get("itemId"), "version": raw.get("version")}
    records._identity(item)
    item["itemType"] = raw["itemType"]
    item["name"] = _required_text(raw, "matchedName")
    for key in ("context", "workflowStatus"):
        item[key] = _required_text(raw, key)
    for key in ("concept", "evsSource", "registrationStatus"):
        if raw.get(key) is not None:
            item[key] = _required_text(raw, key)
    item["provenance"] = records._item_provenance(raw, provenance) | {
        "upstream": {"itemId": raw["itemId"], "version": raw["version"]}
    }
    return item


def _vm_match(raw: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    result = {"item": _vm_item(raw, provenance), "rule": _required_text(raw, "ruleDescription")}
    if raw.get("score") is not None:
        result["score"] = _score(raw)
    if raw.get("crosswalkCode") not in (None, "", "NA"):
        result["crosswalk"] = {
            "code": _required_text(raw, "crosswalkCode"),
            "description": _required_text(raw, "crosswalkDescription"),
        }
    return result


def _vm_matches(
    rows: list[dict[str, Any]], values: list[str], provenance: dict[str, Any]
) -> list[dict[str, Any]]:
    # Validate the complete response before accepting matches, including empty groups.
    if Counter(_required_text(row, "name") for row in rows) != Counter(values):
        records._malformed("value matching groups")
    matches = []
    for row in rows:
        if "matches" not in row:
            records._malformed("matches")
        matches.extend(_vm_match(match, provenance) for match in records._rows(row, "matches"))
    return matches


def match_value_meanings(
    context: Context,
    values: list[str],
    strictness: MatchStrictness = "restricted",
    terminologyScope: list[str] | None = None,  # noqa: N803 - public specification spelling.
    registryRelease: str | None = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """Match 1-10 values to caDSR value meanings and concepts in platform order.

    strictness is restricted (default) or unrestricted, sent as matchType;
    terminologyScope selects EVS code systems. Preserve rules, optional concept/source
    and real crosswalks; empty or NA means no crosswalk and unscored matches have no score.
    Matching uses the configured match timeout (45 seconds by default); failures are
    errors, never empty successes. Results are computed, 0/private. Registry pins fail
    closed because matching cannot yet address published registry releases (C-1).
    """
    inputs = [_text(value, "values") for value in _list(values, "values")]
    headers = _vm_headers(strictness, terminologyScope)
    release = _matching_release(context, registryRelease)
    matches, provenance = match_values(context, inputs, headers, release)
    return _result(matches, provenance)


def match_values(
    context: Context, inputs: list[str], headers: dict[str, str], release: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read validated values using the same state as the surrounding workflow."""
    require("match_value_meanings")
    provenance = records._provenance(context, release, VM_MATCH)
    response = context.cadsr.match_value_meanings([{"name": value} for value in inputs], headers)
    return _vm_matches(response, inputs, provenance), provenance
