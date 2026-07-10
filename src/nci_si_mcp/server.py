"""FastMCP stdio server entrypoint."""

from __future__ import annotations

import sys
from typing import List, Optional

from .config import Settings
from .service import NCISIService


def create_mcp(settings: Optional[Settings] = None):
    if sys.version_info < (3, 10):
        raise RuntimeError(
            "The MCP server requires Python 3.10+ because the upstream 'mcp' "
            f"package does. Current Python is {sys.version.split()[0]}. "
            "Use the core CLI/tests on Python 3.9, or create a Python 3.10+ "
            "environment before running 'serve'."
        )
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on optional runtime install
        raise RuntimeError("FastMCP is not installed. Install project dependencies first.") from exc

    service = NCISIService(settings or Settings.from_env())
    mcp = FastMCP("nci-si-mcp")

    @mcp.tool()
    def ncit_search(query: str, limit: int = 10, mode: str = "hybrid", include_raw: bool = False):
        """Search the active monthly NCIt local index."""
        return service.search(query=query, limit=limit, mode=mode, include_raw=include_raw)

    @mcp.tool()
    def ncit_lookup(code: str, live_only: bool = False, include_raw: bool = False):
        """Look up an NCIt concept with live EVS and active-cache fallback."""
        return service.lookup(code=code, live_only=live_only, include_raw=include_raw)

    @mcp.tool()
    def ncit_traverse(
        start_codes: List[str],
        direction: str = "out",
        max_depth: int = 2,
        max_nodes: int = 200,
        include_hierarchy: bool = True,
        include_roles: bool = True,
        include_associations: bool = True,
        relationship_names: Optional[List[str]] = None,
        edge_types: Optional[List[str]] = None,
    ):
        """Traverse NCIt hierarchy, role, and association edges with filters."""
        return service.traverse(
            start_codes=start_codes,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
            include_hierarchy=include_hierarchy,
            include_roles=include_roles,
            include_associations=include_associations,
            relationship_names=relationship_names,
            edge_types=edge_types,
        )

    @mcp.tool()
    def ncit_release_info():
        """Report EVS release and active index status."""
        return service.release_info()

    @mcp.tool()
    def cadsr_status():
        """Report caDSR adapter state and reuse-spike targets."""
        return service.cadsr_status()

    @mcp.resource("nci-si://concept/ncit/{code}")
    def ncit_concept_resource(code: str):
        return service.lookup(code=code)

    @mcp.resource("nci-si://release/ncit/{version}")
    def ncit_release_resource(version: str):
        info = service.release_info()
        if version in ("monthly", "latest", "monthly-latest"):
            return info
        selected = info["selected_monthly_release"]
        if version == selected["version"]:
            return selected
        return {"isError": True, "error": "release_not_active", "requested_version": version}

    @mcp.resource("nci-si://index/ncit/{version}/manifest")
    def ncit_index_manifest_resource(version: str):
        manifest = service.index.get_active_manifest()
        if not manifest:
            return {"active_index": None}
        if version in ("active", manifest.release_version):
            return manifest.to_dict()
        return {"isError": True, "error": "index_not_active", "requested_version": version}

    return mcp


def run_stdio() -> None:
    create_mcp().run()
