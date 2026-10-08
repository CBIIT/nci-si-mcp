"""Bounded workflows over the same content producers as the fine-grained tools."""

from __future__ import annotations

from itertools import batched
from typing import Any, NotRequired, TypedDict

from . import cadsr_content, cadsr_matching, content, seam
from .bounds import Budget, budgeted, current_budget
from .caching import select_cache_hint
from .catalogue import exclusion_codes, load_catalogue
from .context import Context
from .errors import InputValidationError, PlatformError
from .models import Truncation
from .permissions import require, require_operation
from .release import ReleaseContext
from .release_selection import implicit_selection
from .validation import bounded, validate_identifier


class DictionaryColumn(TypedDict):
    name: str
    description: NotRequired[str]
    sampleValues: NotRequired[list[str]]


def _ground_options(code: str | None, text: str | None, commons: str | None) -> None:
    if (code is None) == (text is None):
        raise InputValidationError("Give exactly one of conceptCode or text", "conceptCode")
    if code is not None:
        validate_identifier(code, r"C[1-9][0-9]*", "conceptCode")
    if text is not None:
        cadsr_matching._text(text, "text")
    if commons is not None:
        cadsr_matching._text(commons, "commons")


def _ground_registry(context: Context, requested: str | None) -> None:
    cadsr_content._pin(context, requested)
    if requested is not None:
        raise PlatformError(
            "capability_unavailable",
            "Shared SI cannot address a registry release. Omit registryRelease until "
            "the provider adds a pin contract (C-1).",
            capability="pinned grounding",
        )


def _text_code(context: Context, text: str, selected: ReleaseContext) -> str:
    require("search_concepts")
    pin = None if implicit_selection() else selected.version
    found = content.search_concepts(context, "ncit", text, pin)["results"]
    if not found:
        raise PlatformError(
            "not_found", "No concept matches. Try other text or give a conceptCode."
        )
    return found[0]["concept"]["code"]


def _hop(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(rows) <= seam.MAX_RESULTS:
        return rows, {"occurred": False}
    return rows[: seam.MAX_RESULTS], Truncation(
        True, "results", seam.MAX_RESULTS, seam.MAX_RESULTS, len(rows) - seam.MAX_RESULTS, False
    ).to_dict()


def _ground_hops(
    context: Context, selected: ReleaseContext, code: str, commons: str | None
) -> dict[str, Any]:
    require("find_data_elements_for_concept")
    graphs = seam._graphs(context, selected)
    provenance = seam._provenance(context, selected, graphs)
    elements = context.ssis.find_data_elements(code, maximum=seam.MAX_RESULTS)
    values = context.ssis.find_permissible_values(code, maximum=seam.MAX_RESULTS)
    hops = {
        "dataElements": [seam._element_use(row, provenance) for row in elements],
        "permissibleValues": [seam._value_use(row, provenance) for row in values],
    }
    if commons is not None:
        hops["storedValues"] = _stored_hop(context, selected, code, commons)
    return _bounded_hops(hops, provenance)


def _bounded_hops(
    hops: dict[str, list[dict[str, Any]]], provenance: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {"provenance": provenance}
    cuts = {}
    for name, rows in hops.items():
        result[name], cuts[name] = _hop(rows)
    cut = next((record for record in cuts.values() if record["occurred"]), None)
    result["truncation"] = (cut | {"perHop": cuts}) if cut else {"occurred": False}
    return result


def _stored_hop(
    context: Context, selected: ReleaseContext, code: str, commons: str
) -> list[dict[str, Any]]:
    require_operation("resolve_stored_value", {"commons": commons})
    if commons != "GDC":
        return seam._crosswalk(context, selected, code, commons, None)["storedValues"]
    return _gdc_hop(context, selected, code)


def _gdc_hop(context: Context, selected: ReleaseContext, code: str) -> list[dict[str, Any]]:
    require("resolve_stored_value")
    source, provenance = seam.gdc_provenance(context, selected)
    result: list[dict[str, Any]] = []
    offset, total = 0, None
    while total is None or offset < total:
        require("resolve_stored_value")
        rows, current_total = context.evs.get_gdc_maps(code, offset)
        if total is not None and current_total != total:
            seam._malformed("changing GDC mapping total", "evs")
        total = current_total
        offset += len(rows)
        result.extend(seam.gdc_values(rows, code, source, provenance))
        if len(result) > seam.MAX_RESULTS:
            return result[: seam.MAX_RESULTS + 1]
    return result


def ground_value(
    context: Context,
    conceptCode: str | None = None,  # noqa: N803
    text: str | None = None,
    commons: str | None = None,
    release: str | None = None,
    registryRelease: str | None = None,  # noqa: N803
) -> dict[str, Any]:
    """Ground a concept or the first default lexical search result across EVS and caDSR.

    Give exactly one of conceptCode/text. No text match is not_found; try other text
    or a conceptCode. Read the chosen concept at the same effective NCIt release.
    Each hop independently holds at most 1000 results; a sentinel cut reports a full
    perHop truncation record. Without commons, storedValues is absent. Every joined
    record names both content states. Registry pins fail closed when unavailable or
    unaddressable; omitted registryRelease is unpinned. One request budget includes
    retries. Omitted release uses the session pin and 0/private caching; explicit
    release joins use the shorter constituent TTL/public.
    """
    _ground_options(conceptCode, text, commons)
    require_operation("ground_value", {"text": text, "commons": commons})
    if release is not None:
        validate_identifier(release, r"[A-Za-z0-9][A-Za-z0-9._-]*", "release")
    with budgeted(current_budget() or Budget()):
        _ground_registry(context, registryRelease)
        selected = seam._selected(context, release)
        code = conceptCode if conceptCode is not None else _text_code(context, text or "", selected)
        pin = None if implicit_selection() else selected.version
        require("get_concept")
        concept = content.get_concept(context, "ncit", code, pin)
        result = {"concept": concept} | _ground_hops(context, selected, code, commons)
    seam._mixed_cache()
    return result


def _column(column: DictionaryColumn) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(column, dict) or set(column) - set(DictionaryColumn.__annotations__):
        raise InputValidationError("Use name, description and sampleValues only", "columns")
    entity = {"entity": cadsr_matching._text(column.get("name"), "columns.name")}
    if "description" in column:
        entity["entityUserTip"] = cadsr_matching._text(column["description"], "columns.description")
    samples = column.get("sampleValues", [])
    if not isinstance(samples, list):
        raise InputValidationError("Must be a list of text values", "columns.sampleValues")
    for sample in samples:
        cadsr_matching._text(sample, "columns.sampleValues")
    return entity, samples


def _align_columns(
    context: Context, samples: list[list[str]], state: dict[str, str]
) -> list[list[dict[str, Any]]]:
    headers = cadsr_matching._vm_headers("restricted", None)
    responses: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    aligned = []
    for values in samples:
        matches = []
        for batch in batched(values, 10, strict=False):
            if batch not in responses:
                responses[batch], _ = cadsr_matching.match_values(
                    context, list(batch), headers, state
                )
            matches.extend(responses[batch])
        aligned.append(matches)
    return aligned


def harmonize_data_dictionary(
    context: Context,
    columns: list[DictionaryColumn],
    registryRelease: str | None = None,  # noqa: N803
    filters: cadsr_matching.MatchFilters | None = None,
) -> dict[str, Any]:
    """Match 1-10 columns and align sample values against one caDSR registry state.

    Each name is an entity and its description is entityUserTip; sample values use
    restricted VM Match in batches of ten. Return columns in caller order, every
    platform match and unmatched names. Identical requests are reused within the call.
    All inputs are validated before requests; failures never become empty matches.
    Omitted registryRelease is unpinned; unavailable or unaddressable pins fail closed.
    One outbound budget counts retries. Caller-computed results are cached 0/private.
    """
    prepared = [_column(column) for column in cadsr_matching._list(columns, "columns")]
    headers = cadsr_matching._headers(filters, 10)
    require_operation("harmonize_data_dictionary", {"columns": columns})
    with budgeted(current_budget() or Budget()):
        state = cadsr_matching._matching_release(context, registryRelease)
        groups, provenance = cadsr_matching.match_entities(
            context, [entity for entity, _ in prepared], headers, state, 10
        )
        alignment = _align_columns(context, [samples for _, samples in prepared], state)
    select_cache_hint(resolution=False, computed=True)
    return _dictionary_result(columns, groups, alignment, provenance)


def _dictionary_result(
    columns: list[DictionaryColumn],
    groups: list[list[dict[str, Any]]],
    alignment: list[list[dict[str, Any]]],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    results = [
        {"name": column["name"], "matches": matches, "permissibleValueAlignment": aligned}
        for column, matches, aligned in zip(columns, groups, alignment, strict=True)
    ]
    return {
        "columns": results,
        "unmatched": [column["name"] for column in results if not column["matches"]],
        "provenance": provenance,
    }


def _exclusions(graph: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [
        {
            "code": edge["targetCode"],
            "terminology": edge["targetTerminology"],
            "edge": edge,
            "provenance": edge["provenance"],
        }
        for edge in graph["edges"]
        if edge["sourceCode"] == code and edge["provenance"].get("polarity") == "negative"
    ]


def _cohort(
    context: Context, selected: ReleaseContext, code: str, budget: Budget, negative: bool
) -> dict[str, Any]:
    depth, maximum = budget.depth, budget.nodes
    require("get_concept_neighborhood")
    exclusions = exclusion_codes(context.settings, "ncit")
    load_catalogue(context.evs, selected, exclusions)
    # Roles are read independently: a small cohort bound must not hide an exclusion.
    budget.depth, budget.nodes, budget.edges = 1, 1000, 5000
    roles = content._graph_record(
        content._graph(context, selected, code, ["role"], budget, exclusions=exclusions)
    )
    excluded = _exclusions(roles, code)
    withheld = set() if negative else {row["code"] for row in excluded}
    budget.depth, budget.nodes = depth, maximum + len(withheld) + 1
    budget.edges = budget.nodes**2
    require("get_concept_hierarchy")
    graph = content._graph_record(content._graph(context, selected, code, ["child"], budget))
    return _cohort_result(graph, roles, excluded, withheld, maximum)


def _cohort_result(
    graph: dict[str, Any],
    roles: dict[str, Any],
    excluded: list[dict[str, Any]],
    withheld: set[str],
    maximum: int,
) -> dict[str, Any]:
    members = [node["code"] for node in graph["nodes"] if node["code"] not in withheld]
    codes = members[:maximum]
    cut = _cohort_cut(graph["truncation"], roles["truncation"], len(members), maximum)
    present = set(codes)
    edges = [edge for edge in graph["edges"] if {edge["sourceCode"], edge["targetCode"]} <= present]
    return {
        "codes": codes,
        "excluded": excluded,
        "edges": edges + [row["edge"] for row in excluded],
        "truncation": cut,
        "provenance": graph["nodes"][0]["provenance"],
    }


def _cohort_cut(
    cut: dict[str, Any], role_cut: dict[str, Any], count: int, maximum: int
) -> dict[str, Any]:
    if count > maximum:
        cut = Truncation(True, "nodes", maximum, maximum, count - maximum, False).to_dict()
    if role_cut["occurred"] and role_cut["bound"] != "depth":
        cut = role_cut
    return cut


def expand_cohort(
    context: Context,
    conceptCode: str,  # noqa: N803
    release: str | None = None,
    maxDepth: int = 2,  # noqa: N803
    includeNegative: bool = False,  # noqa: N803
    maxNodes: int = 200,  # noqa: N803
) -> dict[str, Any]:
    """Expand a cohort through children, excluding only the start concept's negative roles.

    Equivalent to child hierarchy to maxDepth plus the start's depth-one role
    neighborhood. maxDepth defaults to 2 (maximum 4); maxNodes defaults to 200
    (maximum 1000), counting the start. includeNegative defaults to false; true
    retains excluded members but every exclusion assertion is still reported.
    Descendants' exclusion roles do not govern the cohort. Bounds report omitted
    content and one outbound budget includes retries. Omitted release uses the
    session pin and 0/private caching; an explicit release uses long/public.
    """
    validate_identifier(conceptCode, r"C[1-9][0-9]*", "conceptCode")
    require_operation("expand_cohort", {})
    if type(includeNegative) is not bool:
        raise InputValidationError("includeNegative must be boolean", "includeNegative")
    budget = Budget(
        depth=bounded(maxDepth, 4, "maxDepth"), nodes=bounded(maxNodes, 1000, "maxNodes")
    )
    with budgeted(budget):
        selected = seam._selected(context, release)
        result = _cohort(context, selected, conceptCode, budget, includeNegative)
    select_cache_hint(resolution=False, implicit=implicit_selection())
    return result
