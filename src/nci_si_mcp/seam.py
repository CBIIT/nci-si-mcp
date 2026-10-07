"""Cross-domain joins over independently identified upstream content."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any, NoReturn

from . import cadsr_content, content
from . import cursor as cursors
from .bounds import Budget, budgeted, current_budget
from .caching import select_cache_hint
from .cadsr import EXPORT_FOLDER, export_state
from .context import Context
from .errors import InputValidationError, PlatformError, call_correlation_id
from .evs import verify_release
from .models import ProvenanceEnvelope, Truncation, release_ref, utc_now_iso
from .permissions import require, require_operation
from .release import ReleaseContext, resolve_evs_release, served_evs_release
from .release_selection import implicit_selection, select
from .ssis import NCIT_GRAPH, GraphIdentity
from .validation import CrossDomainTerminology, bounded, validate_identifier

MAX_RESULTS = 1000
NCIT_NAMESPACE = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"


def _malformed(section: str, surface: str = "ssis") -> NoReturn:
    raise PlatformError(
        "upstream_unavailable",
        f"The {surface} {section} is malformed. Ask the provider to correct it.",
        surface=surface,
    )


def _text(value: Any, field: str, surface: str = "ssis") -> str:
    if not isinstance(value, str) or not value.strip():
        _malformed(field, surface)
    return value


def _identifier(value: Any, pattern: str, field: str, surface: str = "ssis") -> str:
    value = _text(value, field, surface)
    if re.fullmatch(pattern, value) is None:
        _malformed(field, surface)
    return value


def _version(value: Any) -> tuple[int, int]:
    value = _identifier(value, r"[0-9]+([.][0-9]+)?", "data-element version")
    major, _, minor = value.partition(".")
    return int(major), int(minor or "0")


def _date(value: Any, surface: str = "ssis") -> str:
    value = _text(value, "dataset date", surface)
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        try:
            return datetime.strptime(value, "%B %d, %Y").date().isoformat()
        except ValueError:
            _malformed("dataset date", surface)


def _selected(context: Context, release: str | None) -> ReleaseContext:
    selected = select(context, "ncit", release)
    if release is not None:
        # Explicit releases need a served identity too; no moving alias selects them.
        selected = served_evs_release(
            context.evs.get_terminologies(), "ncit", release, context.settings.release_channel
        )
    return selected


def _graphs(context: Context, selected: ReleaseContext) -> list[GraphIdentity]:
    graphs = context.ssis.get_graph_identities()
    ncit = next(row for row in graphs if row["graph"] == NCIT_GRAPH)
    if "version" not in ncit:
        raise PlatformError(
            "release_not_available",
            "Shared SI has no NCIt release identity. Ask the provider to publish it.",
            requested=selected.version,
            source=NCIT_GRAPH,
        )
    if ncit["version"] != selected.version:
        raise PlatformError(
            "release_mismatch",
            "Shared SI has another NCIt release. Use its release or wait for a graph update.",
            requested=selected.version,
            served=[ncit["version"]],
            source=NCIT_GRAPH,
        )
    return graphs


def _provenance(
    context: Context, selected: ReleaseContext, graphs: list[GraphIdentity]
) -> dict[str, Any]:
    base = ProvenanceEnvelope(
        release=release_ref("ncit", selected.version, selected.date),
        source="ssis_sparql",
        served_by="live",
        retrieved_at=utc_now_iso(),
        correlation_id=call_correlation_id(),
        source_uri=context.ssis.sparql_http.url("/sparql"),
        upstream={"graphs": graphs},
    ).to_dict()
    return base | {
        "graphs": [dict(row) | {"date": _date(row["date"])} for row in graphs],
        "registry": {"registry": "cadsr"},
    }


def _mixed_cache() -> None:
    select_cache_hint(resolution=False, unpinned=True, implicit=implicit_selection())


def _identity(row: dict[str, str]) -> dict[str, str]:
    identifier = _identifier(row.get("id"), r"[1-9][0-9]*", "data-element id")
    _version(row.get("version"))
    return {"publicId": identifier, "version": row["version"]}


def _concept_code(uri: str) -> str:
    uri = _text(uri, "concept IRI")
    if not uri.startswith(NCIT_NAMESPACE):
        _malformed("concept IRI")
    return _identifier(uri.removeprefix(NCIT_NAMESPACE), r"C[1-9][0-9]*", "concept code")


def _element_use(row: dict[str, str], provenance: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataElement": _identity(row) | {"longName": _text(row.get("name"), "data-element name")},
        "provenance": provenance,
    }


def _value_use(row: dict[str, str], provenance: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataElement": _identity(row),
        "value": row["value"],
        "conceptCode": _concept_code(row["concept"]),
        "conceptTerminology": "ncit",
        "provenance": provenance,
    }


def _find_options(code: str, terminology: str, expand: bool, values: bool) -> None:
    validate_identifier(code, r"C[1-9][0-9]*", "conceptCode")
    if terminology != "ncit":
        raise InputValidationError("Only ncit is supported", "terminology")
    for name, value in (("expandDescendants", expand), ("includePermissibleValues", values)):
        if type(value) is not bool:
            raise InputValidationError(f"{name} must be boolean", name)


def _find_rows(
    context: Context, arguments: dict[str, Any]
) -> tuple[list[dict[str, str]], list[dict[str, str]], bool]:
    elements = context.ssis.find_data_elements(
        arguments["conceptCode"],
        expand_descendants=arguments["expandDescendants"],
        maximum=MAX_RESULTS,
    )
    values: list[dict[str, str]] = []
    skipped = arguments["includePermissibleValues"] and len(elements) >= MAX_RESULTS
    if arguments["includePermissibleValues"] and not skipped:
        values = context.ssis.find_permissible_values(
            arguments["conceptCode"],
            expand_descendants=arguments["expandDescendants"],
            maximum=MAX_RESULTS - len(elements),
        )
    return elements, values, skipped


def _page(
    arguments: dict[str, Any],
    token: str | None,
    graphs: list[GraphIdentity],
    rows: tuple[list[dict[str, str]], list[dict[str, str]], bool],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    elements, values, skipped = rows
    # Reuse the opaque cursor content-identity slot for this retrieved snapshot.
    digest = hashlib.sha256(
        json.dumps([sorted(graphs, key=lambda row: row["graph"]), rows], sort_keys=True).encode()
    ).hexdigest()[:32]
    position = cursors.decode(token, arguments, indexed=True)
    if token and position.build_id != digest:
        raise PlatformError("cursor_expired", "The joined content changed. Start a new query.")
    size = min(len(elements) + len(values), MAX_RESULTS)
    if token and position.offset >= size:
        raise InputValidationError("The cursor is past the result", "cursor")
    end = min(position.offset + arguments["limit"], size)
    result = _page_items(
        elements, values, position.offset, end, arguments["includePermissibleValues"], provenance
    )
    result["truncation"] = _truncation(len(elements) + len(values), skipped)
    if end < size:
        result["nextCursor"] = cursors.encode(arguments, end, digest)
    return result


def _truncation(count: int, skipped: bool) -> dict[str, Any]:
    if count <= MAX_RESULTS and not skipped:
        return {"occurred": False}
    return Truncation(
        True, "results", MAX_RESULTS, MAX_RESULTS, max(0, count - MAX_RESULTS), False
    ).to_dict()


def _page_items(
    elements: list[dict[str, str]],
    values: list[dict[str, str]],
    start: int,
    end: int,
    include: bool,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    data = [_element_use(row, provenance) for row in elements[start:end]]
    uses = [
        _value_use(row, provenance)
        for row in values[max(0, start - len(elements)) : max(0, end - len(elements))]
    ]
    result: dict[str, Any] = {"dataElements": data}
    if include:
        result["permissibleValues"] = uses
    if not data and not uses:
        result["provenance"] = provenance
    return result


def find_data_elements_for_concept(
    context: Context,
    conceptCode: str,  # noqa: N803
    terminology: CrossDomainTerminology = "ncit",
    release: str | None = None,
    expandDescendants: bool = False,  # noqa: N803
    includePermissibleValues: bool = False,  # noqa: N803
    limit: int = 100,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Find data-element and optional value uses of an NCIt concept or its descendants.

    Omitted release uses the call/session's NCIt pin. The Shared SI graph must identify
    that release; no unverified REST fallback is used. One page limit (default 100) and
    one 1000-result cap cover data-element uses first, then value uses. Requested values
    remain present on every page, possibly empty. Cursors bind arguments and graph/content
    state. Sentinel cuts report inexact omissions; ordinary paging is not truncation.
    Implicit content uses 0/private; explicit mixed content uses a short public TTL.
    """
    _find_options(conceptCode, terminology, expandDescendants, includePermissibleValues)
    arguments = {
        "tool": "find_data_elements_for_concept",
        "conceptCode": conceptCode,
        "terminology": terminology,
        "release": release,
        "expandDescendants": expandDescendants,
        "includePermissibleValues": includePermissibleValues,
        "limit": bounded(limit, MAX_RESULTS, "limit"),
    }
    cursors.validate_before_selection(cursor, arguments, indexed=True)
    with budgeted(current_budget() or Budget()):
        selected = _selected(context, release)
        graphs = _graphs(context, selected)
        arguments["release"] = selected.version
        cursors.decode(cursor, arguments, indexed=True)
        result = _page(
            arguments,
            cursor,
            graphs,
            _find_rows(context, arguments),
            _provenance(context, selected, graphs),
        )
    _mixed_cache()
    return result


def _value_options(
    identifier: str | None, element: str | None, value: str | None, release: str | None
) -> tuple[str, str]:
    if release is not None:
        validate_identifier(release, r"[A-Za-z0-9][A-Za-z0-9._-]*", "release")
    if identifier is not None:
        validate_identifier(identifier, r"[1-9][0-9]*", "permissibleValueId")
        if element is not None or value is not None:
            raise InputValidationError(
                "Choose permissibleValueId or dataElementId with value", "permissibleValueId"
            )
        raise PlatformError(
            "capability_unavailable",
            "caDSR cannot retrieve a permissible value by id. Use dataElementId with value.",
            capability="OP-C10",
        )
    if not isinstance(element, str):
        raise InputValidationError("dataElementId must be an identifier", "dataElementId")
    validate_identifier(element, r"[1-9][0-9]*", "dataElementId")
    if not isinstance(value, str):
        raise InputValidationError("value must be text", "value")
    return element, value


def _latest_value(rows: list[dict[str, str]], value: str) -> tuple[str, str]:
    if len(rows) > MAX_RESULTS:
        raise PlatformError(
            "upstream_unavailable",
            "The data element has more value rows than the bound. "
            "Ask the provider for a bounded latest-version lookup.",
            surface="ssis",
        )
    latest = max((_version(row["version"]) for row in rows), default=None)
    candidates = [
        row for row in rows if _version(row["version"]) == latest and row.get("value") == value
    ]
    if not candidates:
        raise PlatformError(
            "not_found",
            "The latest data element has no such exact permissible value. Check its spelling.",
        )
    return candidates[0]["version"], _main_code(candidates)


def _main_code(rows: list[dict[str, str]]) -> str:
    codes = {_concept_code(row["concept"]) for row in rows if "concept" in row}
    if len(codes) != 1 or any("concept" not in row for row in rows):
        raise PlatformError(
            "upstream_unavailable",
            "Registry data is ambiguous for that value. "
            "Ask the provider to correct its main concept.",
            surface="ssis",
            candidates=sorted(codes),
        )
    return next(iter(codes))


def get_concept_for_permissible_value(
    context: Context,
    permissibleValueId: str | None = None,  # noqa: N803
    dataElementId: str | None = None,  # noqa: N803
    value: str | None = None,
    release: str | None = None,
) -> dict[str, Any]:
    """Resolve an exact permissible value of a data element to its main NCIt concept.

    Select the latest numeric item version first (2.10 > 2.9), then compare the value
    locally without trimming or case folding. Minor concepts are qualifiers. Missing or
    conflicting main concepts report ambiguous registry data. Caller text never enters
    SPARQL. permissibleValueId is explicitly unavailable (OP-C10). Omitted release uses
    the call/session's NCIt pin; content names both states. Implicit calls use 0/private,
    explicitly pinned mixed results a short public TTL.
    """
    element, value = _value_options(permissibleValueId, dataElementId, value, release)
    require_operation("get_concept_for_permissible_value", {})
    with budgeted(current_budget() or Budget()):
        selected = _selected(context, release)
        graphs = _graphs(context, selected)
        version, code = _latest_value(context.ssis.get_permissible_values(element), value)
        require("get_concept")
        raw = context.evs.get_concept(code, release=selected, include="minimal")
        if raw.get("code") != code:
            _malformed("concept identity", "evs")
        result = content.project_concept(context, selected, raw, [])
        provenance = result["provenance"]
        result["provenance"] = provenance | {
            "registry": {"registry": "cadsr"},
            "graphs": _provenance(context, selected, graphs)["graphs"],
            "upstream": provenance.get("upstream", {}) | {"graphs": graphs},
        }
        result["permissibleValue"] = {
            "dataElement": {"publicId": element, "version": version},
            "value": value,
        }
    _mixed_cache()
    return result


def _stored_options(code: str, commons: str, identifier: str | None) -> None:
    validate_identifier(code, r"C[1-9][0-9]*", "conceptCode")
    if not isinstance(commons, str) or not commons.strip():
        raise InputValidationError("commons must be nonblank text", "commons")
    if identifier is not None:
        validate_identifier(identifier, r"[1-9][0-9]*", "dataElementId")
        if commons == "GDC":
            raise PlatformError(
                "capability_unavailable",
                "GDC cannot select a data element. Omit dataElementId or use a CRDC commons.",
                capability="GDC data-element selector",
            )


def _gdc_rows(context: Context, code: str) -> list[dict[str, Any]]:
    rows, total = context.evs.get_gdc_maps(code)
    if total > MAX_RESULTS:
        raise PlatformError(
            "bound_exceeded",
            "GDC search exceeds the result bound. Ask the provider for an exact-code query.",
            bound="results",
            limit=MAX_RESULTS,
            reached=len(rows),
        )
    while len(rows) < total:
        page, current_total = context.evs.get_gdc_maps(code, len(rows))
        if current_total != total:
            _malformed("changing GDC mapping total", "evs")
        rows.extend(page)
    return rows


def _gdc_value(
    row: dict[str, Any], source: dict[str, str], provenance: dict[str, Any]
) -> dict[str, Any]:
    if (
        row.get("mapsetCode") != source["mapset"]
        or row.get("source") != "ncit"
        or row.get("target") != "GDC"
    ):
        _malformed("GDC map identity", "evs")
    verify_release([{"version": row.get("sourceTerminologyVersion")}], source["version"])
    for key in ("targetName", "targetCode"):
        if not isinstance(row.get(key), str):
            _malformed("GDC stored value", "evs")
    record = {
        "value": row["targetName"],
        "field": row["targetCode"],
        "source": source,
        "provenance": _attributed(row, provenance | {"upstream": row}),
    }
    return record


def _attributed(row: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    if "licenseText" not in row:
        return provenance
    if not isinstance(row["licenseText"], str):
        _malformed("licence text", "evs")
    return provenance | {"attribution": row["licenseText"]}


def _gdc(context: Context, selected: ReleaseContext, code: str) -> dict[str, Any]:
    source, provenance = gdc_provenance(context, selected)
    values = gdc_values(_gdc_rows(context, code), code, source, provenance)
    return _stored_result(values, [source], True, provenance)


def gdc_values(
    rows: list[dict[str, Any]], code: str, source: dict[str, str], provenance: dict[str, Any]
) -> list[dict[str, Any]]:
    """Preserve verified exact-code mappings, never substring matches from term search."""
    return [
        _gdc_value(row, source, provenance)
        for row in rows
        if _identifier(row.get("sourceCode"), r"C[1-9][0-9]*", "GDC source code", "evs") == code
    ]


def gdc_provenance(
    context: Context, selected: ReleaseContext
) -> tuple[dict[str, str], dict[str, Any]]:
    """Verify the mapset identity for both direct resolution and bounded grounding."""
    mapset = context.evs.get_gdc_mapset(selected)
    source = {"mapset": mapset["code"], "version": mapset["version"]}
    provenance = ProvenanceEnvelope(
        release=release_ref("ncit", selected.version, selected.date),
        source="evs_rest",
        served_by="live",
        retrieved_at=utc_now_iso(),
        correlation_id=call_correlation_id(),
        source_uri=context.evs.uri("/api/v1/mapset/NCIt_Maps_To_GDC"),
        upstream=mapset,
    ).to_dict()
    provenance = _attributed(mapset, provenance)
    return source, provenance


def _stored_result(
    values: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    binding: bool,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "storedValues": values,
        "confidence": "asserted" if values else "none",
        "evidence": {"sources": sources, "valueLevelBinding": binding, "coverage": len(values)},
    }
    if not values:
        result["provenance"] = provenance
    return result


def _crosswalk(
    context: Context, selected: ReleaseContext, code: str, commons: str, identifier: str | None
) -> dict[str, Any]:
    maps, base = cadsr_content.read_code_maps(context, {"registry": "cadsr"}, None)
    provenance = base | {
        "release": release_ref("ncit", selected.version, selected.date),
        "registry": {"registry": "cadsr"},
    }
    rows = [
        row
        for row in maps
        if commons in row["usedBy"]
        and (identifier is None or row["dataElement"]["publicId"] == identifier)
    ]
    sources = [{"crosswalk": "CRDC", "dataElement": row["dataElement"]} for row in rows]
    values = _crosswalk_values(rows, sources, code, provenance)
    return _stored_result(
        values, sources, any(row["valueLevelBinding"] for row in rows), provenance
    )


def _crosswalk_values(
    rows: list[dict[str, Any]], sources: list[dict[str, Any]], code: str, provenance: dict[str, Any]
) -> list[dict[str, Any]]:
    values = []
    for row, source in zip(rows, sources, strict=True):
        for value in row["values"]:
            if code in value.get("conceptCode", "").split(":"):
                values.append(
                    {
                        "value": value["value"],
                        "field": _text(row["crdcName"], "CRDC field name", "cadsr"),
                        "source": source,
                        "provenance": row["provenance"]
                        | {"release": provenance["release"], "registry": {"registry": "cadsr"}},
                    }
                )
    return values


def resolve_stored_value(
    context: Context,
    conceptCode: str,  # noqa: N803
    commons: str,
    release: str | None = None,
    dataElementId: str | None = None,  # noqa: N803
) -> dict[str, Any]:
    """Resolve literal stored values through the GDC mapset or the CRDC crosswalk.

    GDC term matches are filtered by exact source code and checked against the effective
    release. Other commons use exact crosswalk membership and colon-separated concept
    bindings. No binding means empty values, none confidence and coverage zero; never a
    preferred-term substitute. dataElementId restricts CRDC; GDC cannot apply that selector.
    Omitted release uses the call/session pin. Implicit content uses 0/private and explicit
    unpinned-source content a short public TTL. All reads share the request budget.
    """
    _stored_options(conceptCode, commons, dataElementId)
    require_operation("resolve_stored_value", {"commons": commons})
    with budgeted(current_budget() or Budget()):
        selected = _selected(context, release)
        result = (
            _gdc(context, selected, conceptCode)
            if commons == "GDC"
            else _crosswalk(context, selected, conceptCode, commons, dataElementId)
        )
    _mixed_cache()
    return result


def _dataset(
    name: str, raw_date: str | None, version: str | None, provenance: dict[str, Any]
) -> dict[str, Any]:
    result = {"name": name, "date": _date(raw_date), "provenance": provenance}
    if version is not None:
        result["version"] = version
    return result


def _alignment_datasets(context: Context) -> list[dict[str, Any]]:
    # Status discovery stays fresh and does not establish or replace a content pin.
    selected = resolve_evs_release(context.evs, "ncit", context.settings.release_channel)
    provenance = ProvenanceEnvelope(
        release=release_ref("ncit", selected.version, selected.date),
        source="evs_rest",
        served_by="live",
        retrieved_at=utc_now_iso(),
        correlation_id=call_correlation_id(),
        source_uri=context.evs.uri("/api/v1/metadata/terminologies"),
        upstream={
            "terminology": selected.terminology,
            "version": selected.version,
            "date": selected.date,
        },
    ).to_dict()
    datasets = [_dataset("ncit", selected.date, selected.version, provenance)]
    graphs = context.ssis.get_graph_identities()
    graph_provenance = _provenance(context, selected, graphs)
    for row in graphs:
        name = "ssis_ncit_graph" if row["graph"] == NCIT_GRAPH else "ssis_cadsr_graph"
        version = row.get("version")
        if row["graph"] == NCIT_GRAPH:
            if version is None:
                raise PlatformError(
                    "release_not_available",
                    "Shared SI has no NCIt release identity. Ask the provider to publish it.",
                    source=NCIT_GRAPH,
                )
            graph_release = release_ref("ncit", version, _date(row["date"]))
        else:
            graph_release = {"registry": "cadsr"}
        datasets.append(
            _dataset(
                name, row["date"], row.get("version"), graph_provenance | {"release": graph_release}
            )
        )
    state = export_state(context.cadsr.export_http.get_text(EXPORT_FOLDER))
    export_provenance = ProvenanceEnvelope(
        release={"registry": "cadsr"},
        source="cadsr_export",
        served_by="live",
        retrieved_at=utc_now_iso(),
        correlation_id=call_correlation_id(),
        source_uri=context.cadsr.export_http.url(EXPORT_FOLDER),
        upstream={
            "generatedAt": state.generated_at,
            "sourceDistribution": state.source_distribution,
        },
    ).to_dict()
    datasets.append(_dataset("cadsr_export", state.generated_at, None, export_provenance))
    return datasets


def get_release_alignment(context: Context, maxIntervalDays: int = 31) -> dict[str, Any]:  # noqa: N803
    """Read NCIt, both Shared SI graphs and the caDSR export as four independent states.

    Dates are ISO calendar dates, without inventing a registry release. intervalDays is
    the largest pairwise interval; warn only when it exceeds maxIntervalDays (default 31,
    nonnegative). A graph difference is reported here, not rejected. Cache policy is 0/public.
    """
    if type(maxIntervalDays) is not int or maxIntervalDays < 0:
        raise InputValidationError(
            "maxIntervalDays must be a nonnegative integer", "maxIntervalDays"
        )
    with budgeted(current_budget() or Budget()):
        datasets = _alignment_datasets(context)
    dates = [date.fromisoformat(row["date"]) for row in datasets]
    interval = (max(dates) - min(dates)).days
    result = {"datasets": datasets, "intervalDays": interval}
    if interval > maxIntervalDays:
        result["warning"] = (
            f"Dataset dates are {interval} days apart, exceeding maxIntervalDays {maxIntervalDays}."
        )
    select_cache_hint(resolution=True)
    return result
