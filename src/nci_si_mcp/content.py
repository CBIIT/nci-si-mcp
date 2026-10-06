"""Caller-pinned EVS content entries and the interim NCIt index."""

from __future__ import annotations

from dataclasses import replace
from itertools import batched
from typing import Any, NoReturn, get_args
from urllib.parse import urlsplit

from . import cursor as cursors
from . import fhir
from .bounds import (
    HARD_MAX_BATCH_CODES,
    HARD_MAX_DEPTH,
    HARD_MAX_EDGES,
    HARD_MAX_NODES,
    HARD_MAX_PER_KIND,
    MAX_BATCH_TARGET_BYTES,
    Budget,
    RequestBudgetError,
    budgeted,
)
from .catalogue import exclusion_codes, load_catalogue
from .context import Context
from .errors import InputValidationError, NoActiveIndexError, PlatformError, call_correlation_id
from .evs import (
    EVSReleaseNotFoundError,
    EVSResponseError,
    concept_path,
    normalize_concept,
    replacements_path,
)
from .models import (
    ProvenanceEnvelope,
    TraversalEdge,
    TraversalProvenance,
    TraversalResult,
    Truncation,
    release_ref,
    upstream_origin,
    utc_now_iso,
)
from .release import ReleaseContext, resolve_evs_release
from .traversal import BATCH_SIZE, traverse_ncit
from .validation import (
    MAX_INDEX_SEARCH_LIMIT,
    ConceptInclude,
    HierarchyDirection,
    NeighborhoodKind,
    PublicSearchMode,
    RetiredSelection,
    bounded,
    validate_choice,
    validate_expansion_options,
    validate_identifier,
    validate_terminology,
)


def _unavailable(capability: str) -> NoReturn:
    raise PlatformError(
        "capability_unavailable",
        f"{capability} is not implemented. Use a supported option or retry after it is available.",
        capability=capability,
    )


def _pin(context: Context, terminology: str, release: str) -> ReleaseContext:
    validate_terminology(terminology)
    validate_identifier(release, r"[A-Za-z0-9][A-Za-z0-9._-]*", "release")
    return ReleaseContext(
        terminology, context.settings.release_channel, release, None, f"{terminology}_{release}"
    )


def _code(code: str, terminology: str) -> str:
    if terminology == "ncit":
        return validate_identifier(code, r"C[1-9][0-9]*", "code")
    return code


def _record(raw: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw.get("active"), bool):
        raise EVSResponseError("EVS returned a concept without its boolean active status")
    if not raw.get("code") or not raw.get("name"):
        raise EVSResponseError("EVS returned a concept without its code or name")
    if raw.get("licenseText"):
        provenance = provenance | {"attribution": raw["licenseText"]}
    result = {
        "code": raw["code"],
        "terminology": raw["terminology"],
        "name": raw["name"],
        "active": raw["active"],
        "provenance": _provenance(provenance) | {"upstream": upstream_origin(raw)},
    }
    if "conceptStatus" in raw:
        result["status"] = raw["conceptStatus"]
    return result


def _provenance(provenance: dict[str, Any]) -> dict[str, Any]:
    # The legacy walker names the direction of navigation. Public hierarchy
    # provenance names the assertion, which points from the child to its parent.
    kind = provenance.get("relationship", {}).get("kind")
    if kind in ("parent", "child"):
        return provenance | {"direction": "out" if kind == "parent" else "in"}
    return provenance


def get_concept(
    context: Context,
    terminology: str,
    release: str,
    code: str,
    include: list[ConceptInclude] | None = None,
) -> dict[str, Any]:
    """Get one EVS concept in the required caller-selected release.

    Returns code, terminology, name, active, upstream status when supplied, and
    live EVS provenance. include optionally selects synonyms, definitions,
    properties and semanticType; without it only the base record is returned.
    Every request is pinned to release and its returned version is checked.
    Codes follow their terminology; an absent code is not_found.
    """
    selected = _pin(context, terminology, release)
    code = _code(code, terminology)
    sections, upstream = _includes(include)
    raw = context.evs.get_concept(
        code,
        release=selected,
        include=upstream,
    )
    if raw.get("code") != code:
        raise EVSResponseError("EVS returned a concept other than the one requested")
    return _project(context, selected, raw, sections)


def _includes(include: list[ConceptInclude] | None) -> tuple[list[ConceptInclude], str]:
    sections = list(dict.fromkeys(include or []))
    for section in sections:
        validate_choice(section, get_args(ConceptInclude), "include")
    upstream = ["properties" if item == "semanticType" else item for item in sections]
    return sections, ",".join(dict.fromkeys(["minimal", *upstream]))


def get_concept_subsets(
    context: Context, terminology: str, release: str, code: str
) -> dict[str, Any]:
    """Read a concept's subset associations in the required caller-selected release.

    Returns subsets in platform order, each with code, terminology, name and
    live source-release provenance. Selects the exact Concept_In_Subset type,
    including computed associations without a relationship code. One pinned
    concept read supplies the associations; malformed content is an error.
    Empty results carry provenance, and supplied licence text passes through.
    """
    rows, provenance = _concept_rows(context, terminology, release, code, "associations")
    subsets = []
    for row in rows:
        if _text_fields(row, ("type",))["type"] == "Concept_In_Subset":
            fields = _text_fields(row, ("relatedCode", "relatedName"))
            subsets.append(
                {
                    "code": fields["relatedCode"],
                    "terminology": terminology,
                    "name": fields["relatedName"],
                    "provenance": _item_provenance(row, provenance),
                }
            )
    return {"subsets": subsets} | ({"provenance": provenance} if not subsets else {})


def expand_value_set(
    context: Context,
    terminology: str,
    release: str,
    valueSet: str | None = None,  # noqa: N803 - public name specified in tools.yaml.
    code: str | None = None,
    count: int = 200,
    offset: int = 0,
    activeOnly: bool = False,  # noqa: N803
) -> dict[str, Any]:
    """Expand an NCIt subset through EVS FHIR with required release verification.

    Supply exactly one of valueSet or code. count defaults to 200 and is capped
    at 1000; count below 1 or offset below 0 is invalid_request. offset defaults
    to 0. activeOnly defaults to false; when true, filter inactive members before
    paging and count only those kept in total. Inactive is present only when true.
    Pages, including clamped pages, are not truncation; past-end pages are empty.
    EVS lacks pinned expansion, so the unpinned answer's version must exactly match
    release before any content is returned. Historical expansion is not promised.
    Each member has FHIR provenance; empty results retain it. Non-NCIt expansion
    is capability_unavailable. HTTP response limits apply to the complete expansion.
    """
    selected = _pin(context, terminology, release)
    supplied = [value for value in (valueSet, code) if value is not None]
    if len(supplied) != 1:
        raise InputValidationError("Supply exactly one of valueSet or code", "valueSet")
    identifier = _code(supplied[0], terminology)
    count = validate_expansion_options(count, offset, activeOnly)
    if terminology != "ncit":
        raise PlatformError(
            "capability_unavailable",
            "EVS enumerates FHIR value sets for NCIt subsets only. Request an NCIt subset.",
            capability="expand_value_set",
        )
    return fhir.expand(context.fhir, selected, identifier, count, offset, activeOnly)


def get_concept_mappings(
    context: Context,
    terminology: str,
    release: str,
    code: str,
    targetTerminology: str | None = None,  # noqa: N803 - public name specified in tools.yaml.
) -> dict[str, Any]:
    """Read maps carried on a concept in the required caller-selected release.

    Returns mappings in platform order, preserving the record's values exactly.
    targetTerminology matches the platform label exactly, including case.
    Target version and term type are omitted when absent, null or empty. Extra
    upstream fields are excluded; missing required fields fail the entire call.
    One pinned concept read supplies maps and source-release provenance; target
    versions remain the map's own. Empty results carry provenance and licence
    text passes through only when supplied by the platform.
    """
    rows, provenance = _concept_rows(context, terminology, release, code, "maps")
    mappings = [_mapping_record(row, provenance) for row in rows]
    if targetTerminology is not None:
        mappings = [row for row in mappings if row["targetTerminology"] == targetTerminology]
    return {"mappings": mappings} | ({"provenance": provenance} if not mappings else {})


def _concept_rows(
    context: Context, terminology: str, release: str, code: str, section: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected = _pin(context, terminology, release)
    code = _code(code, terminology)
    raw = context.evs.get_concept(code, release=selected, include=f"minimal,{section}")
    if raw.get("code") != code:
        raise EVSResponseError("EVS returned a concept other than the one requested")
    rows = raw.get(section, [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise EVSResponseError(f"EVS returned malformed concept {section}")
    return rows, _project(context, selected, raw, [])["provenance"]


def _text_fields(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, str]:
    result = {}
    for field in fields:
        value = row.get(field)
        if not isinstance(value, str) or not value:
            raise EVSResponseError(f"EVS returned a missing or invalid {field}")
        result[field] = value
    return result


def _item_provenance(row: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    return (
        provenance | {"attribution": row["licenseText"]} if row.get("licenseText") else provenance
    )


def _mapping_record(row: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = _text_fields(
        row, ("targetCode", "targetTerminology", "targetName", "type")
    )
    for field in ("targetTermType", "targetTerminologyVersion"):
        if row.get(field) not in (None, ""):
            result.update(_text_fields(row, (field,)))
    return result | {"provenance": _item_provenance(row, provenance)}


def _project(
    context: Context, release: ReleaseContext, raw: dict[str, Any], sections: list[ConceptInclude]
) -> dict[str, Any]:
    uri = context.evs.uri(concept_path(release.pinned_terminology, raw["code"]))
    concept = normalize_concept(raw, release_date=release.date, source="live_evs")
    result = _record(raw, concept.provenance(uri).to_dict())
    return result | {section: _section(raw, section) for section in sections}


def resolve_retired_code(
    context: Context, terminology: str, release: str, code: str
) -> dict[str, Any]:
    """Resolve an EVS code's retirement status and named replacements in a required release.

    Returns code, terminology, the platform's active boolean and optional status
    unchanged, replacements and provenance. Active concepts have no replacement
    and need no history read; status text never determines active. Inactive
    concepts read the pinned single-code replacement history. Each replacement
    preserves the platform's code/name and has its terminology and live provenance.
    A genuine empty history or a retire row naming no replacement yields [].
    Unknown concepts are not_found; an unavailable history endpoint is an error,
    never an empty list. Compact history carries the requested release without
    inventing a self-described upstream version. Supplied licence text passes through.
    """
    selected = _pin(context, terminology, release)
    code = _code(code, terminology)
    concept = get_concept(context, terminology, release, code)
    result = {key: value for key, value in concept.items() if key != "name"}
    result["replacements"] = []
    if not concept["active"]:
        rows = context.evs.get_replacements(code, selected)
        uri = context.evs.uri(replacements_path(selected.pinned_terminology, code))
        result["replacements"] = [
            _replacement_record(row, selected, uri) for row in rows if "replacementCode" in row
        ]
    return result


def _replacement_record(row: dict[str, Any], release: ReleaseContext, uri: str) -> dict[str, Any]:
    code, name = row.get("replacementCode"), row.get("replacementName")
    if not isinstance(code, str) or not code:
        raise EVSResponseError("EVS returned a replacement without its code")
    if not isinstance(name, str) or not name:
        raise EVSResponseError("EVS returned a replacement without its name")
    provenance = ProvenanceEnvelope(
        release=release_ref(release.terminology, release.version, release.date),
        source="evs_rest",
        served_by="live",
        retrieved_at=utc_now_iso(),
        correlation_id=call_correlation_id(),
        source_uri=uri,
        upstream=upstream_origin(row),
    ).to_dict()
    if row.get("licenseText"):
        provenance["attribution"] = row["licenseText"]
    return {
        "code": code,
        "terminology": release.terminology,
        "name": name,
        "provenance": provenance,
    }


def get_concepts(
    context: Context,
    terminology: str,
    release: str,
    codes: list[str],
    include: list[ConceptInclude] | None = None,
) -> dict[str, Any]:
    """Get a batch of concepts in the required release, preserving requested order.

    Returns concepts and missing code lists. Duplicate input occurrences are
    preserved; the upstream request uses each code once. Empty input returns two
    empty lists without a request. include selects synonyms, definitions,
    properties and semanticType, as for get_concept. Each concept carries its
    verified release, status and live provenance; unknown codes are named in missing.
    At most 650 supplied codes and a 7000-byte encoded request target are allowed;
    larger inputs are invalid_request before any upstream call. One nonempty batch
    is one platform call, with counted HTTP retries; there is no per-code fan-out.
    An oversized response fails closed with bound_exceeded, never partial concepts.
    """
    selected = _pin(context, terminology, release)
    requested = _batch_codes(codes, terminology)
    unique = list(dict.fromkeys(requested))
    sections, upstream = _includes(include)
    if not unique:
        return {"concepts": [], "missing": [], "provenance": _empty_provenance(selected)}
    _batch_target(context, selected, unique, upstream)
    raw = context.evs.get_concepts_by_codes(unique, selected, include=upstream)
    found = _reconcile_batch(raw, set(unique))
    result = {
        "concepts": [
            _project(context, selected, found[code], sections)
            for code in requested
            if code in found
        ],
        "missing": [code for code in requested if code not in found],
    }
    if not found:
        result["provenance"] = _empty_provenance(selected)
    return result


def _empty_provenance(release: ReleaseContext) -> dict[str, Any]:
    return ProvenanceEnvelope(
        release=release_ref(release.terminology, release.version, release.date),
        source="evs_rest",
        served_by="live",
        retrieved_at=utc_now_iso(),
        correlation_id=call_correlation_id(),
    ).to_dict()


def _batch_codes(codes: list[str], terminology: str) -> list[str]:
    if len(codes) > HARD_MAX_BATCH_CODES:
        raise InputValidationError(f"codes accepts at most {HARD_MAX_BATCH_CODES} entries", "codes")
    return [_code(code, terminology) for code in codes]


def _batch_target(
    context: Context, release: ReleaseContext, codes: list[str], include: str
) -> None:
    url = urlsplit(
        context.evs.uri(
            concept_path(release.pinned_terminology), {"list": ",".join(codes), "include": include}
        )
    )
    target = f"{url.path}?{url.query}"
    if len(target.encode()) > MAX_BATCH_TARGET_BYTES:
        raise InputValidationError(
            f"The encoded batch request exceeds {MAX_BATCH_TARGET_BYTES} bytes; "
            "request fewer codes",
            "codes",
        )


def _reconcile_batch(raw: list[dict[str, Any]], requested: set[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for item in raw:
        code = item.get("code")
        if not isinstance(code, str) or code not in requested or code in found:
            raise EVSResponseError(
                "EVS returned a missing, unsolicited or duplicate batch identity"
            )
        found[code] = item
    return found


def _section(raw: dict[str, Any], section: str) -> list[Any]:
    if section == "semanticType":
        return [item["value"] for item in raw.get("properties", []) if item.get("code") == "P106"]
    return raw.get(section, [])


def list_relationships(context: Context, terminology: str, release: str) -> dict[str, Any]:
    """List the roles and associations of the required caller-selected release.

    Each relationship has code, terminology, name, kind, polarity and live provenance.
    Polarity follows the configured NCIt exclusion codes (R135 to R142 by default),
    never names; other terminologies have no exclusion set. Each catalogue is read
    once per call, without a cross-call cache. Missing configured codes fail closed
    with internal_error and details.missingCodes; other upstream errors stay explicit.
    All reads are pinned to release and each row's terminology and version are verified.
    The call shares 200 outbound attempts including retries and the HTTP response-size cap.
    """

    selected = _pin(context, terminology, release)
    with budgeted(Budget()):
        relationships = load_catalogue(
            context.evs, selected, exclusion_codes(context.settings, terminology)
        )
    return {"relationships": relationships} | (
        {"provenance": _empty_provenance(selected)} if not relationships else {}
    )


def search_concepts(
    context: Context,
    terminology: str,
    release: str,
    query: str,
    mode: PublicSearchMode = "lexical",
    limit: int = 10,
    cursor: str | None = None,
    retired: RetiredSelection = "include",
) -> dict[str, Any]:
    """Search a pinned terminology, a page at a time, in one of four modes.

    lexical (default) preserves EVS contains order and highlights as matchedOn;
    typeahead preserves its startsWith order without matchedOn. Neither has scores.
    semantic/hybrid rank every concept in the active NCIt index, with scores and
    matchedOn naming name/synonym/definition. Exact preferred names ignore case
    after NFC and whitespace collapsing and win ties; field ties prefer name,
    synonym, definition. Scores are not comparable between queries.
    limit defaults to 10 and clamps at 1000; nextCursor continues with the same
    applied arguments. A served historical release stays valid. Withdrawn releases
    and changed active index builds expire cursors, including same-release rebuilds.
    retired include (default) keeps all statuses; only uses the pinned listing's
    selectable retired status. exclude and upstream search-type options are not offered.
    Missing index or NumPy is capability_unavailable; another index release is
    release_mismatch; an indexed mode for another terminology is invalid_request.
    """
    selected = _pin(context, terminology, release)
    limit = bounded(limit, MAX_INDEX_SEARCH_LIMIT, "limit")
    _search_options(query, mode, retired)
    indexed = mode in ("semantic", "hybrid")
    arguments = {
        "tool": "search_concepts",
        "terminology": terminology,
        "release": release,
        "query": query,
        "mode": mode,
        "limit": limit,
        "retired": retired,
    }
    position = cursors.decode(cursor, arguments, indexed=indexed)
    if indexed and terminology != "ncit":
        raise InputValidationError("The interim index supports only ncit", "terminology")
    try:
        status = _retired_status(context, selected) if retired == "only" else None
        handler = _indexed_search if indexed else _live_search
        return handler(context, selected, arguments, position, status)
    except EVSReleaseNotFoundError:
        if cursor is None:
            raise
        _expired_release(context, selected)


def _indexed_search(
    context: Context,
    selected: ReleaseContext,
    arguments: dict[str, Any],
    position: cursors.Position,
    status: str | None,
) -> dict[str, Any]:
    try:
        hits, total, manifest = context.index.search_page(
            arguments["query"],
            context.embedding_provider,
            arguments["limit"],
            "vector" if arguments["mode"] == "semantic" else "hybrid",
            requested_release=selected.version,
            offset=position.offset,
            build_id=position.build_id,
            retired_status=status,
        )
    except NoActiveIndexError:
        _unavailable("semantic/hybrid search without an active NCIt index")
    results = []
    for hit in hits:
        uri = context.evs.uri(concept_path(selected.pinned_terminology, hit.concept.code))
        results.append(
            {
                "concept": _record(hit.concept.raw, hit.concept.provenance(uri).to_dict()),
                "score": hit.score,
                "matchedOn": hit.matched_on,
            }
        )
    result = _search_page(results, total, arguments, position, manifest.build_id)
    if not results:
        result["provenance"] = manifest.provenance().to_dict()
    return result


def _live_search(
    context: Context,
    selected: ReleaseContext,
    arguments: dict[str, Any],
    position: cursors.Position,
    status: str | None,
) -> dict[str, Any]:
    total, rows = context.evs.search_concepts(
        selected,
        arguments["query"],
        arguments["mode"],
        position.offset,
        arguments["limit"],
        status,
    )
    results = [_live_match(context, selected, row, arguments["mode"], status) for row in rows]
    result = _search_page(results, total, arguments, position)
    if not rows:
        result["provenance"] = ProvenanceEnvelope(
            release=release_ref(selected.terminology, selected.version, selected.date),
            source="evs_rest",
            served_by="live",
            retrieved_at=utc_now_iso(),
            correlation_id=call_correlation_id(),
            source_uri=context.evs.uri(concept_path(selected.pinned_terminology) + "/search"),
        ).to_dict()
    return result


def _live_match(
    context: Context,
    selected: ReleaseContext,
    raw: dict[str, Any],
    mode: str,
    status: str | None,
) -> dict[str, Any]:
    if not isinstance(raw.get("code"), str) or not raw["code"]:
        raise EVSResponseError("EVS search returned a concept without its code")
    if status and (raw.get("conceptStatus") != status or raw.get("active") is not False):
        raise EVSResponseError("EVS search did not honor the requested retired status")
    return {"concept": _project(context, selected, raw, [])} | _live_highlight(raw, mode)


def _live_highlight(raw: dict[str, Any], mode: str) -> dict[str, str]:
    if mode == "lexical" and "highlight" in raw:
        if not isinstance(raw["highlight"], str):
            raise EVSResponseError("EVS search returned a non-text highlight")
        return {"matchedOn": raw["highlight"]}
    return {}


def _search_page(
    results: list[dict[str, Any]],
    total: int,
    arguments: dict[str, Any],
    position: cursors.Position,
    build_id: str | None = None,
) -> dict[str, Any]:
    if position.offset and position.offset >= total:
        raise InputValidationError("The cursor position is beyond this search", "cursor")
    result: dict[str, Any] = {"results": results, "totalKnown": total}
    following = position.offset + len(results)
    if following < total:
        result["nextCursor"] = cursors.encode(arguments, following, build_id)
    return result


def _retirement_metadata(context: Context, selected: ReleaseContext) -> dict[str, Any]:
    rows = [
        row
        for row in context.evs.get_terminologies()
        if row.get("terminology") == selected.terminology and row.get("version") == selected.version
    ]
    if not rows:
        raise EVSReleaseNotFoundError("EVS no longer lists the requested release")
    if len(rows) != 1:
        raise EVSResponseError("EVS lists ambiguous retirement metadata for the pinned release")
    metadata = rows[0].get("metadata", {})
    if not isinstance(metadata, dict):
        raise EVSResponseError("EVS returned malformed retirement metadata")
    return metadata


def _retired_status(context: Context, selected: ReleaseContext) -> str:
    metadata = _retirement_metadata(context, selected)
    status = metadata.get("retiredStatusValue")
    choices = metadata.get("conceptStatuses", [])
    if not isinstance(choices, list):
        raise EVSResponseError("EVS returned malformed concept status choices")
    if not isinstance(status, str) or not status or status not in choices:
        raise InputValidationError("This terminology has no selectable retired status", "retired")
    return status


def _expired_release(context: Context, selected: ReleaseContext) -> NoReturn:
    current = resolve_evs_release(context.evs, selected.terminology, selected.channel)
    raise PlatformError(
        "cursor_expired",
        "EVS no longer serves the cursor release. Restart with the current release.",
        cursorRelease=selected.version,
        currentRelease=current.version,
    ) from None


def _search_options(query: str, mode: str, retired: str) -> None:
    validate_choice(mode, get_args(PublicSearchMode), "mode")
    validate_choice(retired, get_args(RetiredSelection), "retired")
    if not isinstance(query, str) or not query.strip():
        raise InputValidationError("query must not be blank", "query")


def get_concept_hierarchy(
    context: Context,
    terminology: str,
    release: str,
    code: str,
    direction: HierarchyDirection,
    depth: int = 1,
    limit: int = 200,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Get concept parents or children in the required release, excluding the seed.

    depth defaults to 1 and clamps at 4; limit defaults to 200 and clamps at
    1000. Each node has live EVS traversal provenance. The call shares 200
    outbound attempts, retries included. limit bounds a page; nextCursor continues
    in breadth-first platform order with the same applied arguments and release.
    Each continuation replays from the seed within 200 attempts. With batches of
    50, an ordinary depth-one fanout can reach roughly 9,900 nodes including
    replay; retries and oversized responses reduce this. If replay cannot reach
    the page, bound_exceeded asks the caller to narrow the query. A still-served
    historical release remains valid; withdrawal returns cursor_expired with the
    pinned and current releases. Only withdrawal triggers channel discovery.
    pathsToRoot returns every platform path in order and every reached concept
    once; depth, limit and cursor do not apply to this direction.
    A bounded final-frontier check
    reports depth truncation only when unseen targets remain; leaves and cycles
    to returned nodes are complete. Unknown continuation has exact=false.
    A reported global node cut skips this check; already-truncated kinds are excluded.
    """
    selected = _pin(context, terminology, release)
    code = _code(code, terminology)
    validate_choice(direction, get_args(HierarchyDirection), "direction")
    if direction == "pathsToRoot":
        return _paths_to_root(context, selected, code)
    depth, limit = bounded(depth, HARD_MAX_DEPTH, "depth"), bounded(limit, HARD_MAX_NODES, "limit")
    arguments = {
        "terminology": terminology,
        "release": release,
        "code": code,
        "direction": direction,
        "depth": depth,
        "limit": limit,
    }
    offset = cursors.decode(cursor, arguments).offset
    budget = Budget(depth=depth, paged=True)
    # The hierarchy has a page allowance, not a total node or edge allowance.
    # Reserve the seed and a lookahead node; replay remains request-bounded.
    budget.nodes = offset + limit + 2
    budget.edges = budget.nodes**2
    with budgeted(budget):
        graph = _hierarchy_replay(context, selected, code, direction, budget, cursor)
    return _hierarchy_page(graph, arguments, offset, limit)


def _hierarchy_page(
    graph: TraversalResult, arguments: dict[str, Any], offset: int, limit: int
) -> dict[str, Any]:
    # A hierarchy walk has exactly one seed, emitted first.
    projected = _graph_record(graph)["nodes"]
    nodes = projected[1:]
    if offset and offset >= len(nodes):
        raise InputValidationError("The cursor position is beyond this hierarchy", "cursor")
    result: dict[str, Any] = {
        "nodes": nodes[offset : offset + limit],
        "truncation": _truncation(graph.truncation),
    }
    if len(nodes) > offset + limit:
        result["nextCursor"] = cursors.encode(arguments, offset + limit)
    if not nodes:
        result["provenance"] = projected[0]["provenance"]
    return result


def _hierarchy_replay(
    context: Context,
    release: ReleaseContext,
    code: str,
    direction: str,
    budget: Budget,
    cursor: str | None,
) -> TraversalResult:
    try:
        graph = _graph(context, release, code, [direction], budget)
        if budget.exhausted:
            raise RequestBudgetError(budget.requests, budget.attempts)
        return graph
    except EVSReleaseNotFoundError:
        if cursor is None:
            raise
        current = resolve_evs_release(context.evs, release.terminology, release.channel)
        raise PlatformError(
            "cursor_expired",
            "EVS no longer serves the cursor release. Restart with the current release.",
            cursorRelease=release.version,
            currentRelease=current.version,
        ) from None
    except RequestBudgetError as exc:
        raise PlatformError(
            "bound_exceeded",
            "Hierarchy replay exhausted its request budget. Narrow the query with "
            "a nearer starting concept or smaller depth.",
            **exc.details,
        ) from None


def _paths_to_root(context: Context, release: ReleaseContext, code: str) -> dict[str, Any]:
    with budgeted(Budget()):
        seed = context.evs.get_concept(code, release=release, include="minimal")
        if seed.get("code") != code:
            raise EVSResponseError("EVS returned a concept other than the requested path seed")
        paths = context.evs.get_paths_to_root(code, release=release)
    uri = context.evs.uri(
        concept_path(release.pinned_terminology, code) + "/pathsToRoot", {"include": "minimal"}
    )
    nodes = _path_nodes(paths, release, uri, code)
    result: dict[str, Any] = {
        "nodes": list(nodes.values()),
        "paths": [[raw["code"] for raw in path] for path in paths],
        "truncation": {"occurred": False},
    }
    if not nodes:
        result["provenance"] = _record(seed, _path_provenance(release, uri, 0))["provenance"]
    return result


def _path_nodes(
    paths: list[list[dict[str, Any]]], release: ReleaseContext, uri: str, code: str
) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for path in paths:
        for depth, raw in enumerate(path):
            if raw["code"] != code and raw["code"] not in nodes:
                nodes[raw["code"]] = _record(raw, _path_provenance(release, uri, depth))
    return nodes


def _path_provenance(release: ReleaseContext, uri: str, depth: int) -> dict[str, Any]:
    return TraversalProvenance(
        release=release_ref(release.terminology, release.version, release.date),
        source="evs_rest",
        served_by="live",
        retrieved_at=utc_now_iso(),
        correlation_id=call_correlation_id(),
        source_uri=uri,
        depth=depth,
        relationship={"kind": "parent"} if depth else None,
        direction="out" if depth else None,
        polarity="positive" if depth else None,
    ).to_dict()


def get_concept_neighborhood(
    context: Context,
    terminology: str,
    release: str,
    code: str,
    depth: int = 2,
    kinds: list[NeighborhoodKind] | None = None,
    maxNodes: int = 200,  # noqa: N803 - the public signature is specified in tools.yaml.
    maxEdges: int = 1000,  # noqa: N803
    budgetPerKind: int | None = None,  # noqa: N803
    includeNegative: bool = False,  # noqa: N803
) -> dict[str, Any]:
    """Walk terminology relationships in the required release, including the seed at depth 0.

    kinds selects parent, child, role, association, inverseRole or
    inverseAssociation (all six by default). depth defaults to 2, maximum 4;
    maxNodes defaults to 200, maximum 1000, and includes the seed; maxEdges
    defaults to 1000, maximum 5000. budgetPerKind optionally bounds the nodes
    each kind adds, maximum 1000; otherwise kinds take turns within maxNodes.
    Values above maxima clamp. The call shares 200 outbound attempts including
    retries. Nodes and assertion-oriented edges carry live traversal provenance.
    Truncation reports the first bound that omits content. Forward kinds are checked
    at the final frontier within the request budget: unseen targets imply a depth
    cut, while leaves and cycles to returned nodes do not. Selected inverse kinds
    report depth at any nonempty frontier with omitted=0, exact=false, without reading
    their expensive lists solely to count continuation. Continuation is unknown.
    A reported global node cut skips this check; already-truncated kinds are excluded.
    Negative assertions and their targets are returned marked, but their targets
    are not expanded unless includeNegative=true or a positive route reaches
    them. Expansion depth follows that eligible route; a node keeps its first
    arrival provenance. Missing upstream relationship codes remain absent and
    positive; qualifiers and evidence are passed through unchanged.
    Before the walk, the release's role and association catalogues are read once
    in the same budget. Missing configured exclusion codes return internal_error
    with details.missingCodes; no graph is returned and server startup stays offline.
    """
    selected = _pin(context, terminology, release)
    code = _code(code, terminology)
    selected_kinds = _kinds(kinds)
    if not isinstance(includeNegative, bool):
        raise InputValidationError("includeNegative must be boolean", "includeNegative")
    budget = Budget(
        depth=bounded(depth, HARD_MAX_DEPTH, "depth"),
        nodes=bounded(maxNodes, HARD_MAX_NODES, "maxNodes"),
        edges=bounded(maxEdges, HARD_MAX_EDGES, "maxEdges"),
        per_kind=None
        if budgetPerKind is None
        else bounded(budgetPerKind, HARD_MAX_PER_KIND, "budgetPerKind"),
    )
    with budgeted(budget):
        exclusions = exclusion_codes(context.settings, terminology)
        load_catalogue(context.evs, selected, exclusions)
        result = _graph_record(
            _graph(
                context,
                selected,
                code,
                selected_kinds,
                budget,
                exclusions=exclusions,
                include_negative=includeNegative,
            )
        )
    return result


def _kinds(kinds: list[NeighborhoodKind] | None) -> list[str]:
    selected = list(get_args(NeighborhoodKind)) if kinds is None else list(dict.fromkeys(kinds))
    if not selected:
        raise InputValidationError("kinds must not be empty", "kinds")
    for kind in selected:
        validate_choice(kind, get_args(NeighborhoodKind), "kinds")
    aliases = {"inverseRole": "inverse_role", "inverseAssociation": "inverse_association"}
    return [aliases.get(kind, kind) for kind in selected]


def _graph(
    context: Context,
    release: ReleaseContext,
    code: str,
    kinds: list[str],
    budget: Budget,
    *,
    exclusions: frozenset[str] = frozenset(),
    include_negative: bool = True,
) -> TraversalResult:
    with budgeted(budget):
        graph = traverse_ncit(
            context.evs,
            [code],
            release,
            kinds,
            budget,
            exclusions=exclusions,
            include_negative=include_negative,
        )
        truncation = _hydrate(context, graph, release, budget, kinds)
    return replace(graph, truncation=truncation)


def _graph_record(graph: TraversalResult) -> dict[str, Any]:
    nodes = [
        _record(graph.concepts[node.code], node.provenance.to_dict())
        for node in graph.nodes
        if node.code in graph.concepts
    ]
    present = {node["code"] for node in nodes}
    return {
        "nodes": nodes,
        "edges": [
            _edge(edge) for edge in graph.edges if {edge.source_code, edge.target_code} <= present
        ],
        "truncation": _truncation(graph.truncation),
    }


def _hydrate(
    context: Context,
    graph: TraversalResult,
    release: ReleaseContext,
    budget: Budget,
    kinds: list[str],
) -> Truncation:
    missing = [node.code for node in graph.nodes if node.code not in graph.concepts]
    for batch in batched(missing, BATCH_SIZE, strict=False):
        try:
            raw = context.evs.get_concepts_by_codes(batch, release=release, include="minimal")
        except RequestBudgetError:
            # Returning relation names as full concepts would invent their active status.
            # The caller gets the verified portion, with the first omission retained.
            return _hydration_cut(graph, budget, kinds)
        by_code = {item["code"]: item for item in raw}
        if set(by_code) != set(batch):
            raise EVSResponseError("EVS did not return exactly the requested graph concepts")
        graph.concepts.update(by_code)
    return graph.truncation


def _hydration_cut(graph: TraversalResult, budget: Budget, kinds: list[str]) -> Truncation:
    missing = {node.code for node in graph.nodes} - graph.concepts.keys()
    record = graph.truncation if graph.truncation.occurred else _request_cut(budget, len(missing))
    if len(kinds) > 1:
        per_kind = {kind: _hydration_kind_cut(graph, budget, missing, kind) for kind in kinds}
        record = replace(record, per_kind=per_kind)
    return record


def _hydration_kind_cut(
    graph: TraversalResult, budget: Budget, missing: set[str], kind: str
) -> Truncation:
    record = (graph.truncation.per_kind or {}).get(kind, Truncation(False))
    if record.occurred:
        return record
    targets = {edge.target_code for edge in graph.edges if edge.edge_type == kind} & missing
    return _request_cut(budget, len(targets)) if targets else record


def _request_cut(budget: Budget, omitted: int) -> Truncation:
    return Truncation(
        occurred=True,
        bound="requests",
        limit=budget.requests,
        reached=budget.attempts,
        omitted=omitted,
        exact=False,
    )


def _truncation(record: Truncation) -> dict[str, Any]:
    result = record.to_dict()
    aliases = {"inverse_role": "inverseRole", "inverse_association": "inverseAssociation"}
    if "perKind" in result:
        result["perKind"] = {
            aliases.get(kind, kind): value for kind, value in result["perKind"].items()
        }
    return result


def _edge(edge: TraversalEdge) -> dict[str, Any]:
    source, target = edge.source_code, edge.target_code
    if edge.edge_type in ("child", "inverse_role", "inverse_association"):
        source, target = target, source
    return {
        "sourceCode": source,
        "sourceTerminology": edge.provenance.release["terminology"],
        "targetCode": target,
        "targetTerminology": edge.provenance.release["terminology"],
        "provenance": _provenance(edge.provenance.to_dict()),
    }
