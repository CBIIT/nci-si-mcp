"""caDSR registry tools: explicit capabilities, upstream records and content-state provenance."""

from __future__ import annotations

import re
from typing import Any, NoReturn, get_args

from . import cursor as cursors
from .caching import select_cache_hint
from .cadsr import DATA_API, EXPORT_FOLDER, FORM_API, data_element_request
from .context import Context
from .errors import InputValidationError, PlatformError, call_correlation_id
from .models import ProvenanceEnvelope, Truncation, utc_now_iso
from .release import RegistryMetadataError, registry_state
from .validation import (
    CodeMapSource,
    DataElementFilter,
    DataElementInclude,
    RegistrySearchMode,
    bounded,
    validate_choice,
    validate_identifier,
)

_OWN = (
    "publicId",
    "version",
    "longName",
    "context",
    "workflowStatus",
    "registrationStatus",
    "dateCreated",
    "dateModified",
)
_IDENTITY = ("publicId", "version", "longName")
_SEARCH_CAP = 1000
_CODE_MAP_LIMIT = 1000


def _unavailable(capability: str) -> NoReturn:
    raise PlatformError(
        "capability_unavailable",
        f"caDSR does not serve {capability}. Use a supported lookup until the platform adds it.",
        capability=capability,
    )


def _malformed(section: str) -> NoReturn:
    raise PlatformError(
        "upstream_unavailable",
        f"caDSR returned malformed {section}. Ask the operator to check the API.",
        surface="cadsr",
    )


def _rows(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = raw.get(key, [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        _malformed(key)
    return rows


def _object(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        _malformed(key)
    return value


def _fields(raw: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    result = {name: raw.get(name) for name in names}
    if any(value is not None and not isinstance(value, str) for value in result.values()):
        _malformed("record text fields")
    return result


def _pin(context: Context, requested: str | None) -> dict[str, str]:
    release = {"registry": "cadsr"}
    if requested is None:
        return release
    if not isinstance(requested, str) or not requested.strip():
        raise InputValidationError("registryRelease must be nonblank text", "registryRelease")
    row = _registry_row(context, requested)
    state = registry_state(
        row.get("generatedAt"),
        requested,
        source_distribution=context.cadsr.http.url(f"{DATA_API}/registry/releases"),
    )
    return release | {"identifier": requested, "date": state.generated_at}


def _registry_row(context: Context, requested: str) -> dict[str, Any]:
    rows = [
        row for row in context.cadsr.get_registry_releases() if row.get("identifier") == requested
    ]
    if not rows:
        raise PlatformError(
            "release_not_available",
            "caDSR does not publish that registry release. "
            "Omit the pin or use a published release.",
            requested=requested,
            source="cadsr",
        )
    if len(rows) != 1:
        raise RegistryMetadataError("caDSR lists the requested registry release more than once")
    return rows[0]


def _provenance(
    context: Context, release: dict[str, str], path: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    return ProvenanceEnvelope(
        release=release,
        source="cadsr_rest",
        served_by="live",
        retrieved_at=utc_now_iso(),
        correlation_id=call_correlation_id(),
        source_uri=context.cadsr.http.url(path, params),
    ).to_dict()


def _item_provenance(raw: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    origin = {key: raw[key] for key in ("publicId", "version", "dateModified") if key in raw}
    result = provenance | ({"upstream": origin} if origin else {})
    attribution = raw.get("licenseText")
    if attribution is not None:
        if not isinstance(attribution, str):
            _malformed("licence text")
        result = result | {"attribution": attribution}
    return result


def _element(
    raw: dict[str, Any], include: list[DataElementInclude], provenance: dict[str, Any]
) -> dict[str, Any]:
    _identity(raw)
    result = _fields(raw, _OWN) | {"provenance": _item_provenance(raw, provenance)}
    for section in include:
        result[section] = _section(raw, section, provenance)
    return result


def _identity(raw: dict[str, Any]) -> dict[str, Any]:
    _candidate_id(raw)
    if not isinstance(raw.get("version"), str) or not re.fullmatch(
        r"[0-9]+([.][0-9]+)?", raw["version"]
    ):
        _malformed("registry item version")
    return _fields(raw, _IDENTITY)


def _section(raw: dict[str, Any], section: DataElementInclude, provenance: dict[str, Any]) -> Any:
    if section == "alternateNames":
        return _rows(raw, "AlternateNames")
    if section == "classificationSchemes":
        return [_scheme(row, provenance) for row in _rows(raw, "ClassificationSchemes")]
    if section == "conceptAssociations":
        return _associations(_object(raw, "DataElementConcept"))
    return _domain_section(raw, section, provenance)


def _domain_section(raw: dict[str, Any], section: str, provenance: dict[str, Any]) -> Any:
    domain = _object(raw, "ValueDomain")
    if section == "valueDomain":
        return {key: value for key, value in domain.items() if key != "PermissibleValues"}
    return [_value(row, provenance) for row in _rows(domain, "PermissibleValues")]


def _scheme(raw: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    return (
        _identity(raw)
        | _fields(raw, ("context",))
        | {
            "items": [_identity(row) for row in _rows(raw, "ClassificationSchemeItems")],
            "provenance": _item_provenance(raw, provenance),
        }
    )


def _value(raw: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    _candidate_id(raw)
    if not isinstance(raw.get("value"), str):
        _malformed("permissible value")
    meaning = _object(raw, "ValueMeaning")
    return _fields(raw, ("publicId", "value")) | {
        "valueMeaning": _identity(meaning)
        | {"concepts": [_meaning_concept(row) for row in _rows(meaning, "Concepts")]},
        "provenance": _item_provenance(raw, provenance),
    }


def _meaning_concept(raw: dict[str, Any]) -> dict[str, Any]:
    primary = raw.get("primaryIndicator")
    if primary not in ("Yes", "No"):
        _malformed("value-meaning concept primaryIndicator")
    return _concept(raw) | {"primary": primary == "Yes"}


def _concept(raw: dict[str, Any]) -> dict[str, Any]:
    code = raw.get("conceptCode")
    if not isinstance(code, str) or not code.strip():
        _malformed("associated concept code")
    return _fields(raw, ("conceptCode", "longName"))


def _associations(raw: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for key, role in (("ObjectClass", "objectClass"), ("Property", "property")):
        for row in _rows(_object(raw, key), "Concepts"):
            result.append(_concept(row) | {"role": role})
    return result


def _lookup_options(
    public_id: str | None,
    long_name: str | None,
    question: str | None,
    version: str | None,
    include: list[DataElementInclude] | None,
) -> list[DataElementInclude]:
    selectors = {"publicId": public_id, "longName": long_name, "questionText": question}
    if sum(value is not None for value in selectors.values()) != 1:
        raise InputValidationError(
            "Give exactly one of publicId, longName or questionText", "publicId"
        )
    for name, value in selectors.items():
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise InputValidationError(f"{name} must be nonblank text", name)
    return _lookup_fields(public_id, version, include)


def _lookup_fields(
    public_id: str | None, version: str | None, include: list[DataElementInclude] | None
) -> list[DataElementInclude]:
    if public_id is not None:
        validate_identifier(public_id, r"[1-9][0-9]*", "publicId")
    if version is not None:
        validate_identifier(version, r"[0-9]+([.][0-9]+)?", "version")
    for section in include or []:
        validate_choice(section, get_args(DataElementInclude), "include")
    return list(dict.fromkeys(include or []))


def _question_id(context: Context, text: str, pin: str | None) -> str:
    rows = context.cadsr.get_by_question_text(text, registry_release=pin)
    identifiers = list(dict.fromkeys(_candidate_id(row) for row in rows))
    if not identifiers:
        raise PlatformError(
            "not_found",
            "No data element has that preferred question text. Check the text or use a public id.",
            identifiers=[text],
        )
    if len(identifiers) != 1:
        raise InputValidationError(
            "Several data elements have that question text; use a publicId from: "
            + ", ".join(identifiers),
            "questionText",
        )
    return identifiers[0]


def _candidate_id(raw: dict[str, Any]) -> str:
    value = raw.get("publicId")
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value):
        _malformed("registry item identifier")
    return value


def get_data_element(
    context: Context,
    publicId: str | None = None,  # noqa: N803 - public specification spelling.
    longName: str | None = None,  # noqa: N803 - public specification spelling.
    questionText: str | None = None,  # noqa: N803 - public specification spelling.
    version: str | None = None,
    include: list[DataElementInclude] | None = None,
    registryRelease: str | None = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """Get one caDSR data element by public id or preferred question text.

    Give exactly one selector. A unique question resolves to the full item; none is
    not_found and several is invalid_request naming candidates. longName is unavailable
    (OP-C02). version is the item's version. include selects sections, otherwise only the
    base fields are returned. Registry pins must be published and confirmed upstream;
    unpinned content names the registry alone, without the older export's date.
    """
    sections = _lookup_options(publicId, longName, questionText, version, include)
    release = _pin(context, registryRelease)
    if longName is not None:
        _unavailable("longName lookup (OP-C02)")
    identifier = (
        publicId
        if publicId is not None
        else _question_id(context, questionText or "", registryRelease)
    )
    raw = context.cadsr.get_data_element(identifier, version, registry_release=registryRelease)
    if raw is None:
        raise PlatformError(
            "not_found",
            "No data element has that public id and version. Check the identifier or version.",
            identifiers=[identifier],
        )
    _verify_item(raw, identifier, version)
    select_cache_hint(resolution=False, unpinned=registryRelease is None)
    path, params = data_element_request(identifier, version, registryRelease)
    provenance = _provenance(context, release, path, params)
    return _element(raw, sections, provenance)


def _verify_item(raw: dict[str, Any], identifier: str, version: str | None) -> None:
    if raw.get("publicId") != identifier:
        _malformed("data element identity (different from the public id requested)")
    if version is not None and raw.get("version") != version:
        _malformed("data element version (different from the item version requested)")


def search_data_elements(
    context: Context,
    query: str,
    mode: RegistrySearchMode = "lexical",
    filters: dict[DataElementFilter, str] | None = None,
    limit: int = 10,
    cursor: str | None = None,
    registryRelease: str | None = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """Search the requested caDSR keyword route (OP-C03), not served by caDSR today.

    Semantic/hybrid modes and filters are unavailable until their upstream contracts exist.
    A successful upstream list is paged in platform order; 1,000 rows report upstream_cap,
    omitted at least one and exact false. Only a platform count becomes totalKnown.
    Upstream failures stay errors. Persistent failures may reflect the missing operation;
    get_data_element by public id or question text remains available. Cursors bind all applied
    arguments, including verified registry pins. Unpinned content has a short public TTL.
    """
    _search_options(query, mode, filters)
    size = bounded(limit, 100, "limit")
    args = {
        "tool": "search_data_elements",
        "query": query,
        "mode": mode,
        "filters": filters or {},
        "limit": size,
        "registryRelease": registryRelease,
    }
    position = cursors.decode(cursor, args)
    release = _pin(context, registryRelease)
    if mode != "lexical":
        _unavailable(f"{mode} data-element search (OP-C04)")
    if filters:
        _unavailable("data-element search filters (OP-C03, #42)")
    response = context.cadsr.search_data_elements(
        query, _SEARCH_CAP, registry_release=registryRelease
    )
    provenance = _provenance(
        context,
        release,
        f"{DATA_API}/DataElement/search",
        {"keyword": query, "pageSize": _SEARCH_CAP, "registryRelease": registryRelease},
    )
    rows = response["DataElements"]
    records = [{"dataElement": _element(row, [], provenance)} for row in rows[:_SEARCH_CAP]]
    result = _page(records, "results", position.offset, size, args, provenance)
    result["truncation"] = _search_truncation(len(rows), response.get("numRecords"))
    count = response.get("numRecords")
    if type(count) is int and count >= len(rows):
        result["totalKnown"] = count
    select_cache_hint(resolution=False, unpinned=registryRelease is None)
    return result


def _search_options(query: str, mode: str, filters: dict[DataElementFilter, str] | None) -> None:
    if not isinstance(query, str) or not query.strip():
        raise InputValidationError("query must be nonblank text", "query")
    validate_choice(mode, get_args(RegistrySearchMode), "mode")
    _search_filters(filters)


def _search_filters(filters: dict[DataElementFilter, str] | None) -> None:
    if filters is not None and not isinstance(filters, dict):
        raise InputValidationError("filters must be an object", "filters")
    for key, value in (filters or {}).items():
        validate_choice(key, get_args(DataElementFilter), "filters")
        if not isinstance(value, str):
            raise InputValidationError("filter values must be text", "filters")


def _search_truncation(length: int, count: Any) -> dict[str, Any]:
    _validate_count(length, count)
    if length >= _SEARCH_CAP:
        return Truncation(
            True,
            "upstream_cap",
            _SEARCH_CAP,
            _SEARCH_CAP,
            max(1, (count or length) - _SEARCH_CAP),
            False,
        ).to_dict()
    return Truncation(False).to_dict()


def _validate_count(length: int, count: Any) -> None:
    if count is not None and (type(count) is not int or count < length):
        _malformed("search count")
    if count is not None and count > length and length < _SEARCH_CAP:
        _malformed("search response (count exceeds an incomplete list below its cap)")


def _page(
    rows: list[dict[str, Any]],
    key: str,
    offset: int,
    size: int,
    args: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    page = rows[offset : offset + size]
    result: dict[str, Any] = {key: page}
    if offset + size < len(rows):
        result["nextCursor"] = cursors.encode(args, offset + size)
    if not page:
        result["provenance"] = provenance
    return result


def list_contexts(
    context: Context,
    limit: int = 100,
    cursor: str | None = None,
    registryRelease: str | None = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """List caDSR context names, each its identifier, with registry provenance.

    Results retain platform order and use bounded pages. Cursors bind the applied limit
    and registry pin. Unpinned governed content has a short public TTL; caDSR supplies
    no context definitions, so none are invented.
    """
    size = bounded(limit, 1000, "limit")
    args = {"tool": "list_contexts", "limit": size, "registryRelease": registryRelease}
    position = cursors.decode(cursor, args)
    release = _pin(context, registryRelease)
    names = context.cadsr.list_contexts(registry_release=registryRelease)
    provenance = _provenance(
        context, release, "/NCILovAPI/1.0/api/getContextNames", {"registryRelease": registryRelease}
    )
    rows = [{"name": name, "provenance": provenance} for name in names]
    select_cache_hint(resolution=False, unpinned=registryRelease is None)
    return _page(rows, "contexts", position.offset, size, args, provenance)


def list_classification_schemes(
    ctx: Context,
    context: str | None = None,
    limit: int = 100,
    cursor: str | None = None,
    registryRelease: str | None = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """Standalone classification listing is unavailable until caDSR implements OP-C13.

    Use get_data_element with include classificationSchemes for an element's schemes
    and their nested items. No standalone result or synthetic definition is returned.
    """
    if context is not None and (not isinstance(context, str) or not context.strip()):
        raise InputValidationError("context must be nonblank text", "context")
    size = bounded(limit, 1000, "limit")
    cursors.decode(
        cursor,
        {
            "tool": "list_classification_schemes",
            "context": context,
            "limit": size,
            "registryRelease": registryRelease,
        },
    )
    _pin(ctx, registryRelease)
    _unavailable("standalone classification scheme listing (OP-C13)")


def resolve_registry_release(context: Context) -> dict[str, Any]:
    """Resolve published caDSR registry metadata, or the exact export folder row.

    The folder gives local server time without a zone; generatedAt preserves minute
    precision without any offset. No identifier or timezone is invented. This status
    tool is uncached (0/public); its resource is short-lived unpinned content instead.
    """
    select_cache_hint(resolution=True)
    return context.cadsr.resolve_registry_release().to_dict()


def data_element_resource(context: Context, publicId: str) -> dict[str, Any]:  # noqa: N803 - public specification spelling.
    """A caDSR data element at its latest item version, without a registry pin."""
    return get_data_element(context, publicId=publicId)


def data_element_version_resource(context: Context, publicId: str, version: str) -> dict[str, Any]:  # noqa: N803 - public specification spelling.
    """A caDSR data element at the named item version, without a registry pin."""
    return get_data_element(context, publicId=publicId, version=version)


def registry_resource(context: Context) -> dict[str, Any]:
    """The registry state, with source provenance and a short public resource TTL."""
    state = context.cadsr.resolve_registry_release()
    release = {"registry": "cadsr"}
    if state.identifier is not None:
        release |= {"identifier": state.identifier, "date": state.generated_at}
    provenance = ProvenanceEnvelope(
        release=release,
        source="cadsr_rest" if state.identifier else "cadsr_export",
        served_by="live",
        retrieved_at=utc_now_iso(),
        correlation_id=call_correlation_id(),
        source_uri=state.source_distribution
        if state.identifier
        else context.cadsr.export_http.url(EXPORT_FOLDER),
    ).to_dict()
    select_cache_hint(resolution=False, unpinned=True)
    return state.to_dict() | {"provenance": provenance}


def get_form(
    context: Context,
    publicId: str | None = None,  # noqa: N803 - public specification spelling.
    keyword: str | None = None,
    version: str | None = None,
    includeModules: bool = True,  # noqa: N803 - public specification spelling.
    registryRelease: str | None = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """Get a caDSR form by publicId and optional item version, retaining upstream statuses.

    Keyword lookup is invalid_request: Form/query requires an identifier. includeModules
    defaults to true; false omits modules and questions. A validated Form-by-ID HTTP 200
    with explicit form:null/type:E means not_found (the recorded unknown-form response);
    every other failure uses the common error path. Unpinned forms use a short public TTL.
    Published registry pins cannot yet address forms; unlisted pins are unavailable releases.
    """
    identifier = _form_options(publicId, keyword, version, includeModules)
    release = _pin(context, registryRelease)
    if registryRelease is not None:
        raise PlatformError(
            "capability_unavailable",
            "Form lookup lacks a registryRelease field (C-1, upstream requirements package #42). "
            "Omit the pin until caDSR supports it.",
            capability="pinned form lookup",
        )
    raw = context.cadsr.get_form(identifier, version)
    if raw is None:
        raise PlatformError(
            "not_found",
            "No form has that identifier and version. Check the public id or version.",
            identifiers=[identifier],
        )
    item = raw | {"publicId": raw.get("publicID")}
    _verify_item(item, identifier, version)
    provenance = _provenance(
        context, release, f"{FORM_API}/Form/{identifier}", {"version": version}
    )
    result = _element(item, [], provenance)
    result["provenance"] = _item_provenance(raw, provenance) | {
        "upstream": {key: raw[key] for key in ("publicID", "version", "dateModified") if key in raw}
    }
    if includeModules:
        if "modules" not in raw:
            _malformed("form modules")
        result["modules"] = _rows(raw, "modules")
    select_cache_hint(resolution=False, unpinned=True)
    return result


def _form_options(
    identifier: str | None, keyword: str | None, version: str | None, modules: bool
) -> str:
    if keyword is not None:
        raise InputValidationError("The Form API needs an identifier, not a keyword", "keyword")
    validated = validate_identifier(identifier or "", r"[1-9][0-9]*", "publicId")
    if version is not None:
        validate_identifier(version, r"[0-9]+([.][0-9]+)?", "version")
    if not isinstance(modules, bool):
        raise InputValidationError("includeModules must be true or false", "includeModules")
    return validated


def get_permissible_value(
    context: Context,
    permissibleValueId: str,  # noqa: N803 - public specification spelling.
    registryRelease: str | None = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """Standalone permissible-value retrieval is unavailable until caDSR serves OP-C10.

    caDSR REST publishes the value's identifier, but has no operation to retrieve it.
    Invalid identifiers are invalid_request; unlisted registry pins fail before lookup.
    Use get_data_element with include permissibleValues to read the containing value domain.
    """
    validate_identifier(permissibleValueId, r"[1-9][0-9]*", "permissibleValueId")
    _pin(context, registryRelease)
    _unavailable("permissible-value retrieval by identifier (OP-C10)")


def _map_value(raw: dict[str, Any]) -> dict[str, str]:
    value, code = raw.get("Permissible Value"), raw.get("Concept Code")
    if not isinstance(value, str) or (code is not None and not isinstance(code, str)):
        _malformed("code-map value")
    return {"value": value} | ({"conceptCode": code} if code else {})


def _code_map(raw: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    item = {"publicId": raw.get("CDE Public ID"), "version": raw.get("Version")}
    _identity(item)
    names = _fields(raw, ("CRDC Name", "Used By"))
    values = [_map_value(value) for value in _rows(raw, "permissibleValues")]
    return {
        "dataElement": item,
        "crdcName": names["CRDC Name"],
        "usedBy": [name.strip() for name in (names["Used By"] or "").split(",") if name.strip()],
        "valueLevelBinding": bool(values),
        "coverage": sum("conceptCode" in value for value in values),
        "values": values,
        "provenance": _item_provenance(raw, provenance)
        | {"upstream": {key: raw[key] for key in ("CDE Public ID", "Version")}},
    }


def _map_options(source: str, target: str | None, identifier: str | None) -> None:
    validate_choice(source, get_args(CodeMapSource), "sourceSystem")
    if identifier is not None:
        validate_identifier(identifier, r"[1-9][0-9]*", "dataElementId")
    if target is not None and (not isinstance(target, str) or not target.strip()):
        raise InputValidationError("targetContext must be nonblank text", "targetContext")


def get_code_map(
    context: Context,
    sourceSystem: CodeMapSource = "CRDC",  # noqa: N803 - public specification spelling.
    targetContext: str | None = None,  # noqa: N803 - public specification spelling.
    dataElementId: str | None = None,  # noqa: N803 - public specification spelling.
    limit: int = 100,
    cursor: str | None = None,
    registryRelease: str | None = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """Page CRDC code maps, one per data element, with upstream values and concept codes.

    sourceSystem is CRDC only (default). targetContext matches complete comma-split
    context/commons names; dataElementId selects one item. limit is 100 by default, at most
    1000; cursors bind all arguments and registryRelease. No Model API data is guessed.
    Missing values report valueLevelBinding=false and values=[]; coverage counts values
    carrying a concept code, retaining colon-joined codes. Verified pins use a long public
    TTL; unpinned content is short-lived and names the registry alone.
    """
    _map_options(sourceSystem, targetContext, dataElementId)
    size = bounded(limit, _CODE_MAP_LIMIT, "limit")
    args = {
        "tool": "get_code_map",
        "sourceSystem": sourceSystem,
        "targetContext": targetContext,
        "dataElementId": dataElementId,
        "limit": size,
        "registryRelease": registryRelease,
    }
    position = cursors.decode(cursor, args)
    release = _pin(context, registryRelease)
    maps, provenance = _map_records(context, release, registryRelease)
    selected = _select_maps(maps, targetContext, dataElementId)
    select_cache_hint(resolution=False, unpinned=registryRelease is None)
    return _page(selected, "codeMaps", position.offset, size, args, provenance)


def _select_maps(
    maps: list[dict[str, Any]], target: str | None, identifier: str | None
) -> list[dict[str, Any]]:
    return [
        row
        for row in maps
        if (target is None or target in row["usedBy"])
        and (identifier is None or row["dataElement"]["publicId"] == identifier)
    ]


def crosswalk_resource(context: Context) -> dict[str, Any]:
    """The CRDC crosswalk at its 1,000-map maximum, with explicit truncation if larger."""
    maps, provenance = _map_records(context, {"registry": "cadsr"}, None)
    result: dict[str, Any] = {"codeMaps": maps[:_CODE_MAP_LIMIT]}
    if len(maps) > _CODE_MAP_LIMIT:
        result["truncation"] = Truncation(
            True, "results", _CODE_MAP_LIMIT, _CODE_MAP_LIMIT, len(maps) - _CODE_MAP_LIMIT, True
        ).to_dict()
    if not maps:
        result["provenance"] = provenance
    select_cache_hint(resolution=False, unpinned=True)
    return result


def _map_records(
    context: Context, release: dict[str, str], pin: str | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = context.cadsr.get_crdc_list(registry_release=pin)
    provenance = _provenance(
        context, release, f"{DATA_API}/DataElements/getCRDCList", {"registryRelease": pin}
    )
    return [_code_map(row, provenance) for row in rows], provenance
