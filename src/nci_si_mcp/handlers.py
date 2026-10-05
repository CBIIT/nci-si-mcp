"""Domain handlers shared by the MCP and CLI registry."""

from __future__ import annotations

import logging
from collections.abc import Callable
from itertools import batched
from typing import Any

from .audit import emit
from .bounds import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_EDGES,
    DEFAULT_MAX_NODES,
    Budget,
    budgeted,
)
from .caching import RELEASE_REPORT_ALIASES, select_cache_hint
from .context import Context
from .errors import (
    NoActiveIndexError,
    PlatformError,
    call_correlation_id,
    is_error_record,
)
from .evaluation import DEFAULT_GOLD_QUERIES, evaluate_retrieval
from .evs import (
    TERMINOLOGIES_PATH,
    EVSError,
    EVSResponseError,
    concept_path,
    normalize_concept,
    verify_release,
)
from .http_client import UpstreamError, UpstreamUnavailableError
from .invocation import _envelope
from .models import (
    IndexManifest,
    NcitConcept,
    ProvenanceEnvelope,
    release_ref,
    upstream_origin,
    utc_now_iso,
)
from .release import ReleaseContext, current_terminologies, resolve_evs_release
from .traversal import (
    select_edge_types,
    traverse_ncit,
)
from .validation import (
    Direction,
    EdgeType,
    SearchMode,
    validate_channel,
    validate_kind_budget,
    validate_ncit_code,
    validate_ncit_codes,
    validate_search,
    validate_terminology,
    validate_traversal,
)

logger = logging.getLogger(__name__)


def _release(context: Context) -> ReleaseContext:
    """The NCIt release the configured channel names now: resolved for one call, never kept."""

    return resolve_evs_release(context.evs, "ncit", context.settings.release_channel)


def resolve_release(
    context: Context, terminology: str, channel: str | None = None
) -> dict[str, Any]:
    """Resolve the current EVS terminology release by its monthly or weekly channel.

    Omitted channel uses NCI_SI_RELEASE_CHANNEL (monthly by default). Exactly one
    release must be latest within that channel. The result names terminology,
    channel, version and date; alternatives lists the other versions EVS serves
    for that terminology. Pass the selected version to subsequent content calls.
    Provenance identifies the live EVS listing. Discovery is never cached, and
    an unavailable or ambiguous release is an error rather than a guessed version.
    """

    terminology = validate_terminology(terminology)
    channel = validate_channel(context.settings.release_channel if channel is None else channel)
    selected = resolve_evs_release(context.evs, terminology, channel).to_dict()
    return selected | {
        "alternatives": _alternatives(context, selected),
        "provenance": _release_provenance(context, selected).to_dict(),
    }


def _alternatives(context: Context, selected: dict[str, Any]) -> list[str]:
    rows = [
        row
        for row in context.evs.get_terminologies()
        if row.get("terminology") == selected["terminology"]
    ]
    versions = [str(row.get("version") or "") for row in rows]
    if not all(versions):
        raise EVSResponseError("EVS listed an alternative release without a version")
    alternatives = dict.fromkeys(versions)
    alternatives.pop(selected["version"], None)
    return list(alternatives)


def list_terminologies(context: Context) -> dict[str, Any]:
    """List the terminologies EVS serves and each terminology's current release.

    NCIt uses the configured monthly or weekly channel (monthly by default),
    because EVS can mark both channels latest. Other terminologies use their
    sole latest row. Each item names its terminology, release and live EVS
    provenance. Ambiguous or missing current releases fail closed. This current
    listing is resolved anew on every call and is never cached. An empty unfiltered
    upstream listing is unusable metadata (upstream_unavailable), with its actual
    HTTP status and attempt count; no release is invented for empty provenance.
    """

    rows = current_terminologies(context.evs.get_terminologies(), context.settings.release_channel)
    return {
        "terminologies": [
            {
                "terminology": row["terminology"],
                "release": row["version"],
                "provenance": _release_provenance(context, row).to_dict()
                | {"upstream": upstream_origin(row)},
            }
            for row in rows
        ]
    }


def release_info(context: Context) -> dict[str, Any]:
    """Report the EVS API version, the configured channel's NCIt release and the local index.

    The call succeeds even when EVS cannot be reached: `evs_api` and
    `selected_release` (the release the configured channel names, monthly by
    default) then hold an error object, and the report has no `provenance`, which
    otherwise names the selected release. A selected release contains `terminology`,
    `channel`, `version` and `date`; its pinned request path is internal. `active_index` is
    null until an index has been built. `embedding.active_index_compatible`
    says whether the CLI search can use the index: it is false when there is
    none or when it was built with other embedding settings."""

    def evs_status(fetch: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return fetch()
        except (EVSError, UpstreamError, PlatformError) as exc:
            return _envelope("release_info", exc)

    manifest = context.index.get_active_manifest()
    selected = evs_status(lambda: _release(context).to_dict())
    report = {
        "evs_api": evs_status(context.evs.get_api_version),
        "selected_release": selected,
        "active_index": manifest.to_result() if manifest else None,
        "embedding": {
            "provider": context.embedding_provider.name,
            "model": context.embedding_provider.model,
            "active_index_compatible": bool(
                manifest
                and manifest.embedding_matches(
                    context.embedding_provider.name, context.embedding_provider.model
                )
            ),
        },
    }
    # The report is dated and attributed by the release it selected, so there is none to
    # name when EVS could not say.
    if is_error_record(selected):
        return report
    return report | {"provenance": _release_provenance(context, selected).to_dict()}


def _release_provenance(context: Context, selected: dict[str, Any]) -> ProvenanceEnvelope:
    """The provenance of the release report: the selected release, read from EVS now."""

    return ProvenanceEnvelope(
        release=release_ref(selected["terminology"], selected["version"], selected.get("date")),
        source="evs_rest",
        served_by="live",
        retrieved_at=utc_now_iso(),
        correlation_id=call_correlation_id(),
        source_uri=context.evs.uri(TERMINOLOGIES_PATH),
    )


def _fetch_for_index(
    context: Context, codes: list[str], release: ReleaseContext
) -> tuple[list[dict[str, Any]], set[str]]:
    """Fetch the payloads pinned to the release, and the codes EVS returned.

    EVS omits the codes it does not know.
    """

    raw_concepts: list[dict[str, Any]] = []
    for batch in batched(codes, context.settings.index_batch_size, strict=False):
        raw_concepts.extend(
            context.evs.get_concepts_by_codes(batch, terminology=release.pinned_terminology)
        )
    verify_release(raw_concepts, release.version)
    returned_codes = {str(raw.get("code") or "") for raw in raw_concepts}
    if not returned_codes <= set(codes):
        raise EVSResponseError("EVS returned a concept that was not requested")
    return raw_concepts, returned_codes


def index_codes(context: Context, codes: list[str]) -> dict[str, Any]:
    """Index a small list of NCIt codes from the configured channel's current release."""

    normalized_codes = validate_ncit_codes(codes)
    release = _release(context)
    raw_concepts, returned_codes = _fetch_for_index(context, normalized_codes, release)
    missing_codes = [code for code in normalized_codes if code not in returned_codes]
    if missing_codes:
        raise PlatformError(
            "not_found",
            f"NCIt release {release.version} has no concept {', '.join(missing_codes)}; "
            "the index was not changed. Remove the codes, or check them against that release.",
            identifiers=missing_codes,
        )
    manifest = context.index.upsert_concepts(
        raw_concepts=raw_concepts,
        release_date=release.date,
        embedding_provider=context.embedding_provider,
        expected_release_version=release.version,
    )
    emit(
        logger,
        logging.INFO,
        "index_build_complete",
        release=manifest.release_version,
        concepts=manifest.concept_count,
    )
    return manifest.to_result()


def search(
    context: Context,
    query: str,
    limit: int = 10,
    mode: SearchMode = "hybrid",
    include_raw: bool = False,
) -> dict[str, Any]:
    """Search the locally indexed NCIt concepts by text.

    The index holds only the concepts an operator loaded with the
    `index-sample` CLI command, all from the one NCIt release named
    in the `provenance.release` of its hits. It is not all of NCIt, and no
    tool here adds to it. `mode` is `hybrid` (0.55 * BM25 + 0.45 * vector), `bm25` or
    `vector`; `limit` is 1 to 100.

    Each entry of `score_components` is min-max normalized over the
    concepts scored for this query: the best is 1.0 however poor the match,
    the weakest is 0.0 even when it matches, and when only one concept is
    scored, or all tie, they are all 1.0. A component that was not computed
    for a concept (the other one in `bm25` or `vector` mode, or `bm25` for
    a concept without a matching term) is 0.0. Scores therefore order the
    hits of one query and are not comparable across queries. `vector` and
    `hybrid` modes rank by similarity and return up to `limit` concepts
    whether or not anything matches the query; `bm25` returns only
    concepts that share a term with it.

    Each hit's concept carries a `provenance` record: `source` is
    `evs_index`, `servedBy` is `index`, `retrievedAt` is when the concept
    was indexed, and `upstream` holds the terminology and version that EVS
    gave it. A search with no hit carries the `provenance` itself.
    `truncation` is `{occurred: false}` unless `limit` left scored concepts
    out; it then names the `results` bound and how many were `omitted`,
    `exact` where that is a count and not a lower bound."""
    query, limit, normalized_mode = validate_search(query, limit, mode)
    hits, truncation = context.index.search_with_truncation(
        query, context.embedding_provider, limit=limit, mode=normalized_mode
    )
    result: dict[str, Any] = {
        "query": query,
        "mode": normalized_mode,
        "hits": [
            hit.to_dict(_indexed_concept_uri(context, hit.concept), include_raw=include_raw)
            for hit in hits
        ],
        "truncation": truncation.to_dict(),
    }
    # A result with no item has none to carry the provenance (M3.2).
    if not hits:
        result["provenance"] = _active_manifest(context).provenance().to_dict()
    return result


def _active_manifest(context: Context) -> IndexManifest:
    manifest = context.index.get_active_manifest()
    if not manifest:
        raise NoActiveIndexError("No active NCIt index is available")
    return manifest


def _concept_uri(context: Context, code: str, pinned_terminology: str) -> str:
    """The URL of the concept in the release that the terminology segment pins."""

    return context.evs.uri(concept_path(pinned_terminology, code))


def _indexed_concept_uri(context: Context, concept: NcitConcept) -> str:
    """The URL the indexed concept was read from, in the form EVS names a release."""

    return _concept_uri(context, concept.code, f"{concept.terminology}_{concept.release_version}")


def lookup(
    context: Context, code: str, live_only: bool = False, include_raw: bool = False
) -> dict[str, Any]:
    """Look up one NCIt concept by code (C followed by digits) in live EVS.

    The request is pinned to the current release of the configured channel
    (`NCI_SI_RELEASE_CHANNEL`, monthly by default), resolved afresh for this call and
    named by the concept's `provenance.release`; a live answer has
    `provenance.source: evs_rest` and `servedBy: live`, and
    `provenance.upstream` holds the terminology and version EVS gave it. A
    code that release does not contain returns `not_found`. If EVS cannot
    be reached and the concept is in the local index, it is served from
    there instead, with `source: evs_index`, `servedBy: index` and a
    `fallback` object giving the reason; otherwise the call fails with
    `upstream_unavailable`.

    When the local index holds a different release than the current
    one, the call fails with `release_mismatch` for every code, so that
    results from two releases are never mixed. `live_only=true` skips both
    that check and the fallback."""

    code = validate_ncit_code(code)
    manifest = None if live_only else context.index.get_active_manifest()
    try:
        release = _release(context)
        if manifest and manifest.release_version != release.version:
            raise PlatformError(
                "release_mismatch",
                f"The current {release.channel} release is {release.version} but the active "
                f"index holds {manifest.release_version}. Rebuild the index with "
                "`index-sample`, or use live_only to read live EVS without consulting it.",
                requested=release.version,
                served=[manifest.release_version],
                source="index",
            )
        raw = context.evs.get_concept(code, terminology=release.pinned_terminology)
    except UpstreamUnavailableError as exc:
        cached = None if live_only else context.index.get_concept(code)
        if not cached:
            raise
        emit(
            logger,
            logging.WARNING,
            "lookup_cache_fallback",
            code=code,
            errorType=type(exc).__name__,
        )
        result = cached.to_dict(_indexed_concept_uri(context, cached), include_raw=include_raw)
        result["fallback"] = {"reason": "upstream_unavailable", "message": str(exc)}
        return result

    verify_release([raw], release.version)
    concept = normalize_concept(raw, release_date=release.date, source="live_evs")
    uri = _concept_uri(context, concept.code, release.pinned_terminology)
    return concept.to_dict(uri, include_raw=include_raw)


def traverse(
    context: Context,
    start_codes: list[str],
    direction: Direction = "out",
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_edges: int = DEFAULT_MAX_EDGES,
    include_hierarchy: bool = True,
    include_roles: bool = True,
    include_associations: bool = True,
    relationship_names: list[str] | None = None,
    edge_types: list[EdgeType] | None = None,
    budget_per_kind: int | None = None,
) -> dict[str, Any]:
    """Walk NCIt relationships breadth-first from the start codes, in live EVS.

    `direction` `out` follows `child`, `role` and `association` edges, `in`
    follows `parent`, `inverse_role` and `inverse_association` edges, and
    `both` follows all six. The include flags switch hierarchy, role and
    association edges off. `edge_types` narrows the walk to the listed
    types; naming a type that the direction or the include flags exclude
    is an `invalid_request`. It is also the only way to get `descendant`
    edges (direction `out` or `both`, hierarchy included), which link each
    start code directly to every descendant that EVS places within
    `max_depth` levels. EVS gives a descendant one level, which can be
    deeper than its shortest path, so `descendant` edges can miss concepts
    that a `child` walk of the same depth reaches: up to a few percent at
    depth 2, and up to a third at depth 3 or 4, depending on the concept.
    Use `child` edges when every concept within `max_depth` is needed.
    `relationship_names` keeps only edges with those names, ignoring case:
    role and association names such as `Disease_Has_Finding`, or
    `is_a_parent`, `is_a_child` and `is_a_descendant` for hierarchy edges.

    Limits are clamped to depth 4, 1,000 nodes and 5,000 edges, and the
    result reports the effective `max_depth`, `max_nodes` and `max_edges`.
    `budget_per_kind` optionally limits new nodes per relationship kind,
    clamped to 1,000. Kinds take turns across the whole frontier at each
    depth; starts count against the global node limit, and edges to
    existing nodes spend no kind allowance. Mixed-kind truncation includes
    `perKind` records. A traversal shares 200 HTTP attempts across release
    discovery, retries and split batches. Exhaustion before any graph is
    available returns `bound_exceeded`; otherwise it returns a partial graph
    with `requests` truncation unless an earlier bound already dropped something.
    An exhausted kind uses `kind_budget`. Unread kinds report a lower-bound
    omitted count of zero with `exact: false` when their relation count is unknown.
    Nearer nodes claim the limits before farther ones. `truncation` is
    `{occurred: false}` unless something was dropped. It then names the
    first `bound` that still omits something: `depth`, `nodes`, `edges`, `kind_budget`,
    `requests`, or `upstream_cap`, when the relations or descendants of a concept
    were too large for the EVS response limit to read, which raising the
    node and edge limits does not help (for descendants, a smaller
    `max_depth` can). `limit` is that bound's value, `reached` what had
    been counted, and `omitted` a lower bound on nodes, edges or unread work
    left out; `exact` is false, since what lies beyond a dropped
    item was never read. A bounded final-frontier check of selected relation lists
    reports `depth` only when unseen targets remain, counting distinct targets
    one level further. Leaves and cycles to returned nodes are complete.
    The check spends the same request budget; an earlier bound still wins.
    A reported global node cut skips it; kinds already truncated are excluded.
    Descendant checks use final child lists. Inverse kinds never read final lists
    only to count continuation: each selected inverse kind at a nonempty frontier reports
    depth with omitted=0 and exact=false, without claiming a leaf or continuation.
    Every edge connects two nodes of the result. Every node and edge
    carries a `provenance` record: the release of the configured channel all data is read
    from, and how the item was reached: its `depth` (an edge has that of
    the node it reaches), and for any item but the start codes the
    `relationship` `{kind, code?, name?}` (a role or association has a
    code and name, a hierarchy link only its kind), the `direction` in
    which that edge type is followed and the `polarity`, `negative` for
    the exclusion roles R135 to R142 by code. A start code that release
    does not contain returns `not_found`."""
    validate_kind_budget(budget_per_kind)
    start_codes, normalized_direction, selected_types, relationship_names = validate_traversal(
        start_codes,
        direction,
        max_depth,
        max_nodes,
        max_edges,
        edge_types,
        relationship_names,
    )
    selected = select_edge_types(
        normalized_direction, include_hierarchy, include_roles, include_associations, selected_types
    )
    budget = Budget(depth=max_depth, nodes=max_nodes, edges=max_edges, per_kind=budget_per_kind)
    with budgeted(budget):
        return traverse_ncit(
            context.evs,
            start_codes,
            _release(context),
            selected,
            budget,
            relationship_names=relationship_names,
        ).to_dict()


def evaluate(context: Context) -> dict[str, Any]:
    """Score BM25, vector and hybrid ranking on the built-in gold queries."""

    results = evaluate_retrieval(context.index, context.embedding_provider)
    gold_codes = {code for gold in DEFAULT_GOLD_QUERIES for code in gold.expected_codes}
    return {
        "results": [result.to_dict() for result in results],
        # A gold concept that is not indexed can never be found.
        "gold_codes_not_indexed": sorted(
            code for code in gold_codes if not context.index.get_concept(code)
        ),
    }


def concept_resource(context: Context, code: str) -> dict[str, Any]:
    """One NCIt concept, as returned by the lookup handler with default options."""

    return lookup(context, code)


def release_resource(context: Context, version: str) -> dict[str, Any]:
    """The current channel's release, or the full status report for a moving alias."""

    info = release_info(context)
    if version in RELEASE_REPORT_ALIASES:
        select_cache_hint(resolution=True)
        return info
    selected = info["selected_release"]
    if is_error_record(selected) or version == selected["version"]:
        return selected
    raise PlatformError(
        "release_not_available",
        f"Release {version} is not served here; the current {selected['channel']} release is "
        f"{selected['version']}. Read that release, or use `current`.",
        requested=version,
        source="evs",
    )


def index_resource(context: Context, version: str) -> dict[str, Any]:
    """The local index's manifest by version or active alias, or its absent status."""

    active = context.index.get_active_manifest()
    manifest = active.to_result() if active else None
    if not manifest:
        select_cache_hint(resolution=True)
        return {"active_index": None}
    if version in ("active", manifest["release_version"]):
        select_cache_hint(resolution=version == "active")
        return manifest
    raise PlatformError(
        "release_not_available",
        f"The local index holds release {manifest['release_version']}, not "
        f"{version}. Read that release or `active`, or rebuild the index with `index-sample`.",
        requested=version,
        source="index",
    )
