"""caDSR registry tools: explicit capabilities, upstream records and content-state provenance."""

from __future__ import annotations

import re
from typing import Annotated, Any, NoReturn, get_args

from . import cursor as cursors
from .caching import select_cache_hint
from .cadsr import DATA_API, EXPORT_FOLDER, FORM_API, data_element_request
from .context import Context
from .errors import InputValidationError, PlatformError, call_correlation_id
from .models import ProvenanceEnvelope, Truncation, utc_now_iso
from .parameters import (
    REGISTRY_ID_FORM,
    REGISTRY_VERSION_FORM,
    Cursor,
    Described,
    RegistryRelease,
    SearchFilters,
    count_bound,
)
from .permissions import require
from .release import RegistryMetadataError, registry_state
from .validation import (
    CodeMapSource,
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
    publicId: Annotated[  # noqa: N803 - public specification spelling.
        str | None,
        Described(
            "Public id of the data element, for example 2179689. Give exactly one of "
            "publicId, longName and questionText.",
            pattern=REGISTRY_ID_FORM,
        ),
    ] = None,
    longName: Annotated[  # noqa: N803 - public specification spelling.
        str | None,
        Described(
            "Required by the caDSR SOW; caDSR does not serve it yet (OP-C02), so a value "
            "is refused with capability_unavailable. Leave unset until it does."
        ),
    ] = None,
    questionText: Annotated[  # noqa: N803 - public specification spelling.
        str | None,
        Described(
            "The preferred question text of the data element; it resolves to the one "
            "element that has it. Give exactly one of publicId, longName and "
            "questionText."
        ),
    ] = None,
    version: Annotated[
        str | None,
        Described(
            "Version of the data element, for example 1.0. Leave unset for the latest.",
            pattern=REGISTRY_VERSION_FORM,
        ),
    ] = None,
    include: Annotated[
        list[DataElementInclude] | None,
        Described(
            "Sections to add: any of permissibleValues, valueDomain, conceptAssociations, "
            "alternateNames, classificationSchemes. Leave unset for the element's own "
            "fields only."
        ),
    ] = None,
    registryRelease: RegistryRelease = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """Read one caDSR data element by its publicId or its preferred question text.

    registryRelease is left unset today (C-1: caDSR publishes no registry release).

    Returns the data element with provenance (the registry alone for unpinned content); nested
    permissible values and schemes carry provenance. Its conceptAssociations section lists the
    concepts behind the element.

    not_found when no element has that publicId and version or that question text;
    invalid_request naming the candidates when several have the text. longName is not served by
    caDSR today and is capability_unavailable (OP-C02). A pin must be published and confirmed
    upstream, else release_not_available or release_mismatch.
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
    query: Annotated[str, Described("Words to search for, for example breast cancer stage.")],
    mode: Annotated[
        RegistrySearchMode,
        Described(
            "How to search: lexical, the default. semantic and hybrid are requested from "
            "caDSR (OP-C04) and are refused with capability_unavailable until it serves "
            "them."
        ),
    ] = "lexical",
    filters: Annotated[
        SearchFilters | None,
        Described(
            "Required by the caDSR SOW (filtering by context, workflow status, "
            "registration status and value domain type); the keyword route does not serve "
            "filters yet (OP-C03), so a value is refused with capability_unavailable. "
            "Leave unset until it does."
        ),
    ] = None,
    limit: Annotated[int, count_bound("Most results on a page.", 10, 100)] = 10,
    cursor: Cursor = None,
    registryRelease: RegistryRelease = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """Search caDSR data elements by keyword, a page at a time; caDSR does not serve this route
    today (OP-C03).

    semantic and hybrid (OP-C04) and filters (OP-C03) are capability_unavailable until caDSR
    serves them.

    Returns results, each a dataElement in platform order, with truncation and nextCursor. A
    list of 1,000 rows reports upstream_cap, omitted at least one and exact false. totalKnown
    appears only when the platform gives a count.

    upstream_unavailable when the route fails; a persistent failure may reflect the missing
    operation, so read an element by publicId or question text instead.
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
        _unavailable("data-element search filters (OP-C03, docs/upstream/cadsr.md#cadsr-search)")
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
    if type(count) is int:
        result["totalKnown"] = count
    select_cache_hint(resolution=False, unpinned=registryRelease is None)
    return result


def _search_options(query: str, mode: str, filters: SearchFilters | None) -> None:
    if not isinstance(query, str) or not query.strip():
        raise InputValidationError("query must be nonblank text", "query")
    validate_choice(mode, get_args(RegistrySearchMode), "mode")
    _search_filters(filters)


def _search_filters(filters: SearchFilters | None) -> None:
    if filters is not None and not isinstance(filters, dict):
        raise InputValidationError("filters must be an object", "filters")
    for key, value in (filters or {}).items():
        validate_choice(key, tuple(SearchFilters.__annotations__), "filters")
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
    if offset and offset >= len(rows):
        # The list is fetched again per page; one that shrank is not a clean last page.
        raise InputValidationError("The cursor position is beyond this list", "cursor")
    page = rows[offset : offset + size]
    result: dict[str, Any] = {key: page}
    if offset + size < len(rows):
        result["nextCursor"] = cursors.encode(args, offset + size)
    if not page:
        result["provenance"] = provenance
    return result


def list_contexts(
    context: Context,
    limit: Annotated[int, count_bound("Most contexts on a page.", 100, 1000)] = 100,
    cursor: Cursor = None,
    registryRelease: RegistryRelease = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """List the caDSR contexts, the owner groups under which data elements are registered, by name.

    registryRelease is left unset today (C-1).

    Returns contexts in platform order, each its name with registry provenance, and nextCursor,
    bound to the applied limit and pin. caDSR supplies no context definitions, so none are
    invented.

    upstream_unavailable when caDSR cannot answer; release_not_available for a pin that is not
    published.
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
    context: Annotated[
        str | None,
        Described(
            "caDSR context whose schemes to list, for example NCIP. The standalone "
            "listing is requested from caDSR (OP-C13) and is not served yet; leave "
            "unset."
        ),
    ] = None,
    limit: Annotated[int, count_bound("Most schemes on a page.", 100, 1000)] = 100,
    cursor: Cursor = None,
    registryRelease: RegistryRelease = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """List caDSR classification schemes with their nested items; caDSR does not serve this today
    (OP-C13).

    Returns, once the platform lists them, classificationSchemes as objects with their nested
    items and nextCursor. No standalone result and no synthetic definition is returned today.

    capability_unavailable until caDSR implements the listing (OP-C13); an element's own schemes
    are still read with its other sections. Invalid input is invalid_request and an unpublished
    pin release_not_available, both checked first.
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
    """Report the caDSR registry's content state: its published release, or the export that stands
    in for one.

    No arguments.

    Returns the registry release where caDSR publishes one. While it publishes none (C-1),
    published is false and the result gives the date of the exact export folder row: local
    server time without a zone, kept to the minute. No identifier or timezone is invented, and
    the export date is never a registry release.

    upstream_unavailable when the export folder or registry metadata cannot be read.
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
    publicId: Annotated[  # noqa: N803 - public specification spelling.
        str | None,
        Described("Public id of the form, for example 2200604.", pattern=REGISTRY_ID_FORM),
    ] = None,
    keyword: Annotated[
        str | None,
        Described(
            "Required by the caDSR SOW; the platform's Form/query takes a public or "
            "protocol id only, so a keyword is refused with invalid_request. Leave unset "
            "and give publicId."
        ),
    ] = None,
    version: Annotated[
        str | None,
        Described(
            "Version of the form, for example 1.0. Leave unset for the latest.",
            pattern=REGISTRY_VERSION_FORM,
        ),
    ] = None,
    includeModules: Annotated[  # noqa: N803 - public specification spelling.
        bool,
        Described(
            "Return the modules and questions of the form. Default true; false returns "
            "the form's own fields only."
        ),
    ] = True,
    registryRelease: RegistryRelease = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """Read one caDSR form or case report form by its publicId, with its modules and questions.

    A keyword is not accepted: the platform needs an identifier. registryRelease is left unset
    today (C-1).

    Returns the form with its statuses unchanged (a retired form stays retired) and, unless left
    out, modules and questions in platform order, with provenance.

    not_found when the platform reports an unknown form; invalid_request for a keyword or a bad
    id; capability_unavailable for a published registry pin until C-1 defines how forms are
    pinned, release_not_available for an unpublished one; upstream_unavailable otherwise.
    """
    identifier = _form_options(publicId, keyword, version, includeModules)
    release = _pin(context, registryRelease)
    if registryRelease is not None:
        raise PlatformError(
            "capability_unavailable",
            "Form lookup lacks a registryRelease field (C-1, docs/upstream/cadsr.md#cadsr-forms). "
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
    permissibleValueId: Annotated[  # noqa: N803 - public specification spelling.
        str,
        Described(
            "Identifier of the permissible value, for example 2200636. The tool is "
            "requested from caDSR (OP-C10) and is not served yet, so the call is answered "
            "with capability_unavailable.",
            pattern=REGISTRY_ID_FORM,
        ),
    ],
    registryRelease: RegistryRelease = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """Read one caDSR permissible value by its identifier; caDSR cannot retrieve one standalone
    today (OP-C10).

    permissibleValueId is the identifier caDSR REST publishes for the value; registryRelease is
    left unset today (C-1).

    Returns nothing today. caDSR publishes the identifier but has no operation that retrieves a
    value by it.

    capability_unavailable (OP-C10); invalid_request for a malformed identifier and
    release_not_available for an unpublished pin, both checked first. Read the containing value
    domain with the element's permissibleValues section instead.
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
    sourceSystem: Annotated[  # noqa: N803 - public specification spelling.
        CodeMapSource,
        Described("Source code system of the maps; only CRDC is served. Default CRDC."),
    ] = "CRDC",
    targetContext: Annotated[  # noqa: N803 - public specification spelling.
        str | None,
        Described(
            "Only the maps of this context or commons name, for example GDC. Leave unset for all."
        ),
    ] = None,
    dataElementId: Annotated[  # noqa: N803 - public specification spelling.
        str | None,
        Described(
            "Only the map of this one data element, for example 2179689.", pattern=REGISTRY_ID_FORM
        ),
    ] = None,
    limit: Annotated[int, count_bound("Most code maps on a page.", 100, 1000)] = 100,
    cursor: Cursor = None,
    registryRelease: RegistryRelease = None,  # noqa: N803 - public specification spelling.
) -> dict[str, Any]:
    """Page the CRDC code maps that tie a CRDC field to its data element and the values it may
    store, with concept codes.

    The only way from a CRDC field name to a data element: page the maps and take the one whose
    crdcName equals the field. targetContext matches its complete name among the comma-split Used
    By names. registryRelease is left unset today (C-1).

    Returns codeMaps, one per data element, each with crdcName, usedBy, valueLevelBinding,
    coverage (values that carry a concept code; colon-joined codes are kept) and values; with no
    value-level binding, valueLevelBinding is false and values []. nextCursor continues, bound
    to all arguments. Nothing is guessed from the Model API.

    invalid_request for a source other than CRDC, a bad id, or a cursor used with other
    arguments; upstream_unavailable when the crosswalk cannot be read.
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
    maps, provenance = read_code_maps(context, release, registryRelease)
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
    maps, provenance = read_code_maps(context, {"registry": "cadsr"}, None)
    result: dict[str, Any] = {"codeMaps": maps[:_CODE_MAP_LIMIT]}
    if len(maps) > _CODE_MAP_LIMIT:
        result["truncation"] = Truncation(
            True, "results", _CODE_MAP_LIMIT, _CODE_MAP_LIMIT, len(maps) - _CODE_MAP_LIMIT, True
        ).to_dict()
    if not maps:
        result["provenance"] = provenance
    select_cache_hint(resolution=False, unpinned=True)
    return result


def read_code_maps(
    context: Context, release: dict[str, str], pin: str | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read and project the crosswalk once, before a caller filters or pages it."""
    require("get_code_map")
    rows = context.cadsr.get_crdc_list(registry_release=pin)
    provenance = _provenance(
        context, release, f"{DATA_API}/DataElements/getCRDCList", {"registryRelease": pin}
    )
    return [_code_map(row, provenance) for row in rows], provenance
