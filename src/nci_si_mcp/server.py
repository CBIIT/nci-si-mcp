"""MCP stdio server entrypoint."""

from __future__ import annotations

import json
from typing import Any

from . import __version__
from .config import Settings, configure_logging
from .errors import error_response
from .service import NCISIService
from .traversal import DEFAULT_MAX_DEPTH, DEFAULT_MAX_EDGES, DEFAULT_MAX_NODES
from .validation import Direction, EdgeType, SearchMode

INSTRUCTIONS = (
    "NCI Thesaurus (NCIt) lookup and relationship traversal against live NCI EVS, "
    "plus text search over a small locally indexed sample of concepts. Concept, search "
    "and traversal results name the NCIt monthly release they came from. A failed tool "
    "call is flagged as an error. Failures the server handles carry a JSON object with "
    "isError, error (a stable code), message and optional details; arguments rejected "
    "by the tool schema are reported as plain text."
)


def create_mcp(settings: Settings | None = None, *, service: NCISIService | None = None):
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ResourceError
        from mcp.types import CallToolResult, TextContent
    except ImportError as exc:
        raise RuntimeError(
            "The MCP server needs the 'server' extra, which installs mcp>=2,<3 "
            f"(pip install -e '.[server]'). Import failed: {exc}"
        ) from exc

    resolved_settings = settings or Settings.from_env()
    configure_logging(resolved_settings.log_level)
    service = service or NCISIService(resolved_settings)
    mcp = MCPServer("nci-si-mcp", instructions=INSTRUCTIONS, version=__version__)

    def tool_result(result: dict[str, Any]) -> Any:
        """Flag an error envelope as an error at the protocol level as well."""

        if result.get("isError"):
            text = json.dumps(result, indent=2)
            return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)
        return result

    def resource_result(result: dict[str, Any]) -> dict[str, Any]:
        if result.get("isError"):
            raise ResourceError(json.dumps(result))
        return result

    @mcp.tool()
    def ncit_search(
        query: str,
        limit: int = 10,
        mode: SearchMode = "hybrid",
        include_raw: bool = False,
    ):
        """Search the locally indexed NCIt concepts by text.

        The index holds only the concepts an operator loaded with the
        `index-sample` CLI command, all from the one NCIt monthly release named
        in `release_version`. It is not all of NCIt, and no tool here adds to
        it. `mode` is `hybrid` (0.55 * BM25 + 0.45 * vector), `bm25` or
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

        Hits have `source: active_cache`, and `retrieved_at` is when the
        concept was indexed. `include_raw` adds the full EVS payload.
        """
        return tool_result(
            service.search(query=query, limit=limit, mode=mode, include_raw=include_raw)
        )

    @mcp.tool()
    def ncit_lookup(code: str, live_only: bool = False, include_raw: bool = False):
        """Look up one NCIt concept by code (C followed by digits) in live EVS.

        The request is pinned to the current monthly release, and a live answer
        has `source: live_evs`. A code that release does not contain returns
        `concept_not_found`. If EVS cannot be reached and the concept is in the
        local index, it is served from there instead, with
        `source: active_cache` and a `fallback` object giving the reason;
        otherwise the call fails with `evs_unavailable`.

        When the local index holds a different release than the current monthly
        one, the call fails with `version_mismatch` for every code, so that
        results from two releases are never mixed. `live_only=true` skips both
        that check and the fallback. `include_raw` adds the full EVS payload.
        """
        return tool_result(
            service.lookup(code=code, live_only=live_only, include_raw=include_raw)
        )

    @mcp.tool()
    def ncit_traverse(
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
        Nearer nodes claim the limits before farther ones. `truncated` is true
        when something was dropped: by the node limit, by the edge limit, or
        because the relations or descendants of a concept were too large to
        read, in which case `unexpanded_codes` lists it and raising the limits
        does not help (for descendants, a smaller `max_depth` can). Stopping
        at `max_depth` does not set `truncated`.
        Every edge connects two nodes of the result, and all data is read from
        the monthly release named in `release_version`. A start code that
        release does not contain returns `concept_not_found`.
        """
        return tool_result(
            service.traverse(
                start_codes=start_codes,
                direction=direction,
                max_depth=max_depth,
                max_nodes=max_nodes,
                max_edges=max_edges,
                include_hierarchy=include_hierarchy,
                include_roles=include_roles,
                include_associations=include_associations,
                relationship_names=relationship_names,
                edge_types=list(edge_types) if edge_types else None,
            )
        )

    @mcp.tool()
    def ncit_release_info():
        """Report the EVS API version, the current monthly NCIt release and the local index.

        The call succeeds even when EVS cannot be reached: `evs_api` and
        `selected_monthly_release` then hold an error object. `active_index` is
        null until an index has been built. `embedding.active_index_compatible`
        says whether `ncit_search` can use the index: it is false when there is
        none or when it was built with other embedding settings.
        """
        return tool_result(service.release_info())

    @mcp.tool()
    def cadsr_status():
        """Report that caDSR common data element search is not implemented yet.

        Returns `state: reuse_pending` and the integrations under evaluation.
        It never returns CDE data.
        """
        return service.cadsr_status()

    @mcp.resource("nci-si://concept/ncit/{code}", mime_type="application/json")
    def ncit_concept_resource(code: str):
        """One NCIt concept, as returned by the `ncit_lookup` tool with default options."""
        return resource_result(service.lookup(code=code))

    @mcp.resource("nci-si://release/ncit/{version}", mime_type="application/json")
    def ncit_release_resource(version: str):
        """The current monthly NCIt release.

        `monthly`, `latest` and `monthly-latest` return the full status report
        of the `ncit_release_info` tool. The version of the current monthly
        release returns that release's record; any other version is an error.
        """
        info = resource_result(service.release_info())
        if version in ("monthly", "latest", "monthly-latest"):
            return info
        selected = resource_result(info["selected_monthly_release"])
        if version == selected["version"]:
            return selected
        return resource_result(
            error_response(
                "release_not_active",
                f"Release {version} is not the current monthly release {selected['version']}",
                requested_version=version,
            )
        )

    @mcp.resource("nci-si://index/ncit/{version}/manifest", mime_type="application/json")
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
        return resource_result(
            error_response(
                "index_not_active",
                f"The local index holds release {manifest['release_version']}, not {version}",
                requested_version=version,
            )
        )

    return mcp
