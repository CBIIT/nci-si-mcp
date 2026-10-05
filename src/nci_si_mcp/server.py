"""MCP stdio server entrypoint."""

from __future__ import annotations

import functools
import inspect
import json
from collections.abc import Callable
from typing import Any, cast

from . import __version__
from .bounds import DEFAULT_MAX_DEPTH, DEFAULT_MAX_EDGES, DEFAULT_MAX_NODES
from .config import Settings, configure_logging
from .errors import PlatformError, correlated, is_error_record, serialise
from .service import NCISIService
from .validation import Direction, EdgeType, SearchMode

INSTRUCTIONS = (
    "NCI Thesaurus (NCIt) lookup and relationship traversal against live NCI EVS, "
    "plus text search over a small locally indexed sample of concepts. Every item a tool "
    "returns carries a provenance record that names the NCIt monthly release it came from, "
    "the surface that supplied it and the call's correlationId. A failed tool "
    "call is flagged as an error. Failures the server handles carry the error record "
    "{error: {code, message, details?, correlationId}}: code is one of invalid_request, "
    "not_found, release_not_available, release_mismatch, upstream_unavailable, timeout, "
    "bound_exceeded, capability_unavailable, cursor_expired or internal_error, and message "
    "names the next step; correlationId echoes the _meta.correlationId of the call, or is "
    "generated; arguments rejected by the tool schema are reported as plain text."
)


def create_mcp(settings: Settings | None = None, *, service: NCISIService | None = None):
    # The mcp package is an optional extra, so it is imported only when a server is built.
    try:
        from mcp.server.mcpserver import Context, MCPServer
        from mcp.server.mcpserver.exceptions import ResourceError
        from mcp.types import CallToolResult, TextContent
    except ImportError as exc:
        raise RuntimeError(
            "The MCP server needs the 'server' extra, which installs mcp>=2,<3 "
            f"(pdm install). Import failed: {exc}"
        ) from exc

    resolved_settings = settings or Settings.from_env()
    configure_logging(resolved_settings.log_level)
    service = service or NCISIService(resolved_settings)
    mcp = MCPServer("nci-si-mcp", instructions=INSTRUCTIONS, version=__version__)

    def tool_result(ctx: Any, call: Callable[[], dict[str, Any]]) -> Any:
        """Run one tool call under its correlation identifier (the request's
        `_meta.correlationId`, else a generated one), and flag an error record as an
        error at the protocol level as well."""

        meta = ctx.request_context.meta or {}
        with correlated(meta.get("correlationId")):
            result = call()
        if is_error_record(result):
            text = json.dumps(result, indent=2)
            return CallToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content=result,
                is_error=True,
            )
        return result

    def resource_result(result: dict[str, Any]) -> dict[str, Any]:
        if is_error_record(result):
            raise ResourceError(json.dumps(result))
        return result

    def tool(fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register a tool whose `ctx` parameter receives the request context."""

        # The annotation is set here because `Context` is imported only when a server is built.
        if "ctx" in inspect.signature(fn).parameters:
            fn.__annotations__["ctx"] = Context
        return mcp.tool()(fn)

    _register_tools(tool, service, tool_result)
    _register_resources(mcp, service, resource_result)
    return mcp


def _register_tools(
    tool: Callable[[Callable[..., Any]], Callable[..., Any]],
    service: NCISIService,
    tool_result: Callable[[Any, Callable[[], dict[str, Any]]], Any],
) -> None:
    """Register the tools. Their docstrings are the contract sent to MCP clients."""

    @tool
    def ncit_search(
        query: str,
        limit: int = 10,
        mode: SearchMode = "hybrid",
        ctx: Any = None,
    ):
        """Search the locally indexed NCIt concepts by text.

        The index holds only the concepts an operator loaded with the
        `index-sample` CLI command, all from the one NCIt monthly release named
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
        `exact` where that is a count and not a lower bound.
        """
        return tool_result(ctx, lambda: service.search(query=query, limit=limit, mode=mode))

    @tool
    def ncit_lookup(code: str, live_only: bool = False, ctx: Any = None):
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
        that check and the fallback.
        """
        return tool_result(ctx, lambda: service.lookup(code=code, live_only=live_only))

    @tool
    def ncit_traverse(
        start_codes: list[str],
        direction: Direction = "out",
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_edges: int = DEFAULT_MAX_EDGES,
        budget_per_kind: int | None = None,
        include_hierarchy: bool = True,
        include_roles: bool = True,
        include_associations: bool = True,
        relationship_names: list[str] | None = None,
        edge_types: list[EdgeType] | None = None,
        ctx: Any = None,
    ):
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
        first `bound` that still omits something: `nodes`, `edges`, `kind_budget`,
        `requests`, or `upstream_cap`, when the relations or descendants of a concept
        were too large for the EVS response limit to read, which raising the
        node and edge limits does not help (for descendants, a smaller
        `max_depth` can). `limit` is that bound's value, `reached` what had
        been counted, and `omitted` a lower bound on nodes, edges or unread work
        left out; `exact` is false, since what lies beyond a dropped
        item was never read. Stopping at `max_depth` is no truncation.
        Every edge connects two nodes of the result. Every node and edge
        carries a `provenance` record: the release of the configured channel all data is read
        from, and how the item was reached: its `depth` (an edge has that of
        the node it reaches), and for any item but the start codes the
        `relationship` `{kind, code?, name?}` (a role or association has a
        code and name, a hierarchy link only its kind), the `direction` in
        which that edge type is followed and the `polarity`, `negative` for
        the exclusion roles R135 to R142 by code. A start code that release
        does not contain returns `not_found`.
        """
        return tool_result(
            ctx,
            lambda: service.traverse(
                start_codes=start_codes,
                direction=direction,
                max_depth=max_depth,
                max_nodes=max_nodes,
                max_edges=max_edges,
                budget_per_kind=budget_per_kind,
                include_hierarchy=include_hierarchy,
                include_roles=include_roles,
                include_associations=include_associations,
                relationship_names=relationship_names,
                edge_types=list(edge_types) if edge_types else None,
            ),
        )

    @tool
    def ncit_release_info(ctx: Any = None):
        """Report the EVS API version, the configured channel's NCIt release and the local index.

        The call succeeds even when EVS cannot be reached: `evs_api` and
        `selected_monthly_release` (the release the configured channel names, monthly by
        default) then hold an error object, and the report has no `provenance`, which
        otherwise names the selected release. A selected release contains `terminology`,
        `channel`, `version` and `date`; its pinned request path is internal. `active_index` is
        null until an index has been built. `embedding.active_index_compatible`
        says whether `ncit_search` can use the index: it is false when there is
        none or when it was built with other embedding settings.
        """
        return tool_result(ctx, service.release_info)

    @tool
    def cadsr_status():
        """Report that caDSR common data element search is not implemented yet.

        Returns `state: reuse_pending` and the integrations under evaluation.
        It never returns CDE data.
        """
        return service.cadsr_status()


def _per_call[Resource: Callable[..., Any]](resource: Resource) -> Resource:
    """Read a resource under one correlation identifier, shared by every record of the read."""

    @functools.wraps(resource)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with correlated():
            return resource(*args, **kwargs)

    return cast("Resource", wrapper)


def _register_resources(
    mcp: Any, service: NCISIService, resource_result: Callable[[dict[str, Any]], dict[str, Any]]
) -> None:
    def release_not_available(message: str, requested: str, source: str) -> dict[str, Any]:
        error = PlatformError("release_not_available", message, requested=requested, source=source)
        return resource_result(serialise(error))

    @mcp.resource("nci-si://concept/ncit/{code}", mime_type="application/json")
    @_per_call
    def ncit_concept_resource(code: str):
        """One NCIt concept, as returned by the `ncit_lookup` tool with default options."""
        return resource_result(service.lookup(code=code))

    @mcp.resource("nci-si://release/ncit/{version}", mime_type="application/json")
    @_per_call
    def ncit_release_resource(version: str):
        """The current NCIt release of the configured channel (monthly by default).

        `monthly`, `latest` and `monthly-latest` return the full status report
        of the `ncit_release_info` tool, even when the configured channel is weekly.
        The current version returns `{terminology, channel, version, date}`;
        any other version is an error.
        """
        info = resource_result(service.release_info())
        if version in ("monthly", "latest", "monthly-latest"):
            return info
        selected = resource_result(info["selected_monthly_release"])
        if version == selected["version"]:
            return selected
        return release_not_available(
            f"Release {version} is not served here; the current {selected['channel']} release is "
            f"{selected['version']}. Read that release, or use `monthly`.",
            version,
            "evs",
        )

    @mcp.resource("nci-si://index/ncit/{version}/manifest", mime_type="application/json")
    @_per_call
    def ncit_index_manifest_resource(version: str):
        """The manifest of the local search index.

        `active`, or the release the index holds, returns the manifest; any
        other version is an error. Without an index the result is
        `{"active_index": null}`.
        """
        result = resource_result(service.index_manifest())
        manifest = result["active_index"]
        if not manifest:
            return result
        if version in ("active", manifest["release_version"]):
            return manifest
        return release_not_available(
            f"The local index holds release {manifest['release_version']}, not "
            f"{version}. Read that release or `active`, or rebuild the index with "
            "`index-sample`.",
            version,
            "index",
        )
