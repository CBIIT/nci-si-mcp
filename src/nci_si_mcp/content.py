"""Caller-pinned EVS content entries over the existing NCIt retrieval engines."""

from __future__ import annotations

from dataclasses import replace
from itertools import batched
from typing import Any, NoReturn, get_args

from .bounds import Budget, RequestBudgetError, budgeted
from .context import Context
from .errors import InputValidationError, NoActiveIndexError, PlatformError
from .evs import EVSResponseError, concept_path, normalize_concept, verify_release
from .index import require_index_release
from .models import TraversalEdge, TraversalResult, Truncation, upstream_origin
from .release import ReleaseContext
from .traversal import BATCH_SIZE, traverse_ncit
from .validation import (
    ConceptInclude,
    HierarchyDirection,
    NeighborhoodKind,
    PublicSearchMode,
    RetiredSelection,
    bounded,
    validate_choice,
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
    if terminology != "ncit":
        _unavailable("content for terminologies other than ncit")
    return ReleaseContext(
        terminology, context.settings.release_channel, release, None, f"ncit_{release}"
    )


def _code(code: str) -> str:
    return validate_identifier(code, r"C[1-9][0-9]*", "code")


def _record(raw: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    if raw.get("terminology") != "ncit":
        raise EVSResponseError("EVS returned a concept from another terminology")
    if not isinstance(raw.get("active"), bool):
        raise EVSResponseError("EVS returned a concept without its boolean active status")
    if not raw.get("code") or not raw.get("name"):
        raise EVSResponseError("EVS returned a concept without its code or name")
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
    """Get one NCIt concept in the required caller-selected release.

    Returns code, terminology, name, active, upstream status when supplied, and
    live EVS provenance. include optionally selects synonyms, definitions,
    properties and semanticType; without it only the base record is returned.
    Every request is pinned to release and its returned version is checked.
    Other terminologies are capability_unavailable; an absent code is not_found.
    """
    selected = _pin(context, terminology, release)
    code = _code(code)
    sections = list(dict.fromkeys(include or []))
    for section in sections:
        validate_choice(section, get_args(ConceptInclude), "include")
    upstream_sections = ["properties" if item == "semanticType" else item for item in sections]
    raw = context.evs.get_concept(
        code,
        terminology=selected.pinned_terminology,
        include=",".join(dict.fromkeys(["minimal", *upstream_sections])),
    )
    verify_release([raw], release)
    if raw.get("code") != code:
        raise EVSResponseError("EVS returned a concept other than the one requested")
    uri = context.evs.uri(concept_path(selected.pinned_terminology, code))
    concept = normalize_concept(raw, release_date=None, source="live_evs")
    result = _record(raw, concept.provenance(uri).to_dict())
    return result | {section: _section(raw, section) for section in sections}


def _section(raw: dict[str, Any], section: str) -> list[Any]:
    if section == "semanticType":
        return [item["value"] for item in raw.get("properties", []) if item.get("code") == "P106"]
    return raw.get(section, [])


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
    """Search the interim NCIt index with semantic or hybrid ranking.

    release is required and must equal the index's release (release_mismatch
    otherwise). The index contains only operator-loaded concepts, not all NCIt.
    limit defaults to 10 and clamps at 1000. Results contain concept records,
    scores and index provenance, with truncation when scored hits are omitted.
    Scores order this query's hits and are not comparable between queries.
    The default lexical mode, typeahead, cursors and retired only are currently
    capability_unavailable; retired include returns all indexed statuses.
    Other terminologies are capability_unavailable. No live fallback is used.
    """
    _pin(context, terminology, release)
    limit = bounded(limit, 1000, "limit")
    _search_options(query, mode, cursor, retired)
    try:
        hits, truncation = context.index.search_with_truncation(
            query,
            context.embedding_provider,
            limit,
            "vector" if mode == "semantic" else "hybrid",
            requested_release=release,
        )
    except NoActiveIndexError:
        _unavailable("semantic/hybrid search without an active NCIt index")
    results = []
    for hit in hits:
        uri = context.evs.uri(concept_path(f"ncit_{release}", hit.concept.code))
        results.append(
            {
                "concept": _record(hit.concept.raw, hit.concept.provenance(uri).to_dict()),
                "score": hit.score,
            }
        )
    result: dict[str, Any] = {"results": results, "truncation": truncation.to_dict()}
    if not hits:
        manifest = context.index.get_active_manifest()
        if manifest is None:
            _unavailable("semantic/hybrid search without an active NCIt index")
        require_index_release(manifest, release)
        result["provenance"] = manifest.provenance().to_dict()
    return result


def _search_options(query: str, mode: str, cursor: str | None, retired: str) -> None:
    validate_choice(mode, get_args(PublicSearchMode), "mode")
    validate_choice(retired, get_args(RetiredSelection), "retired")
    if not isinstance(query, str) or not query.strip():
        raise InputValidationError("query must not be blank", "query")
    if mode in ("lexical", "typeahead") or cursor is not None or retired != "include":
        _unavailable("lexical/typeahead search, search cursors and retired-only selection")


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
    """Get NCIt parents or children in the required release, excluding the seed.

    depth defaults to 1 and clamps at 4; limit defaults to 200 and clamps at
    1000. Each node has live EVS traversal provenance. The call shares 200
    outbound attempts, retries included. pathsToRoot, cursors and results that
    need paging are capability_unavailable until hierarchy paging is implemented.
    Other terminologies are capability_unavailable. A bounded final-frontier check
    reports depth truncation only when unseen targets remain; leaves and cycles
    to returned nodes are complete. Unknown continuation has exact=false.
    A reported global node cut skips this check; already-truncated kinds are excluded.
    """
    selected = _pin(context, terminology, release)
    code = _code(code)
    validate_choice(direction, get_args(HierarchyDirection), "direction")
    depth, limit = bounded(depth, 4, "depth"), bounded(limit, 1000, "limit")
    if direction == "pathsToRoot" or cursor is not None:
        _unavailable("hierarchy pathsToRoot and cursors")
    budget = Budget(depth=depth, nodes=limit)
    # Hierarchy pages exclude the seed and return no edges. Allow the seed
    # separately, and every possible directed pair within that node allowance.
    budget.nodes += 1
    budget.edges = budget.nodes**2
    graph = _graph(context, selected, code, [direction], budget)
    if graph.node_limit_hit:
        _unavailable("hierarchy paging")
    result = _graph_record(graph)
    hierarchy = {
        "nodes": [node for node in result["nodes"] if node["code"] != code],
        "truncation": result["truncation"],
    }
    if not hierarchy["nodes"]:
        hierarchy["provenance"] = result["nodes"][0]["provenance"]
    return hierarchy


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
    """Walk NCIt relationships in the required release, including the seed at depth 0.

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
    Negative assertions are marked; following beyond their targets requires
    includeNegative=true until selective negative expansion is implemented,
    otherwise capability_unavailable. Other terminologies are unavailable.
    """
    selected = _pin(context, terminology, release)
    code = _code(code)
    selected_kinds = _kinds(kinds)
    if not isinstance(includeNegative, bool):
        raise InputValidationError("includeNegative must be boolean", "includeNegative")
    budget = Budget(
        depth=bounded(depth, 4, "depth"),
        nodes=bounded(maxNodes, 1000, "maxNodes"),
        edges=bounded(maxEdges, 5000, "maxEdges"),
        per_kind=None if budgetPerKind is None else bounded(budgetPerKind, 1000, "budgetPerKind"),
    )
    result = _graph_record(_graph(context, selected, code, selected_kinds, budget))
    if not includeNegative and any(
        edge["provenance"].get("polarity") == "negative"
        and edge["provenance"]["depth"] < budget.depth
        for edge in result["edges"]
    ):
        _unavailable("selective negative assertion expansion")
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
    context: Context, release: ReleaseContext, code: str, kinds: list[str], budget: Budget
) -> TraversalResult:
    with budgeted(budget):
        graph = traverse_ncit(context.evs, [code], release, kinds, budget)
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
            raw = context.evs.get_concepts_by_codes(
                batch, terminology=release.pinned_terminology, include="minimal"
            )
        except RequestBudgetError:
            # Returning relation names as full concepts would invent their active status.
            # The caller gets the verified portion, with the first omission retained.
            return _hydration_cut(graph, budget, kinds)
        verify_release(raw, release.version)
        by_code = {item["code"]: item for item in raw}
        if set(by_code) != set(batch):
            raise EVSResponseError("EVS did not return exactly the requested graph concepts")
        graph.concepts.update(by_code)
    return graph.truncation


def _hydration_cut(graph: TraversalResult, budget: Budget, kinds: list[str]) -> Truncation:
    missing = {node.code for node in graph.nodes} - graph.concepts.keys()
    record = graph.truncation
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
        "sourceTerminology": "ncit",
        "targetCode": target,
        "targetTerminology": "ncit",
        "provenance": _provenance(edge.provenance.to_dict()),
    }
