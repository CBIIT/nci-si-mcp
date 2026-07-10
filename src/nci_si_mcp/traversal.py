"""Bounded NCIt traversal with named relationship filtering."""

from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .evs import EVSClient
from .models import TraversalEdge, TraversalNode, TraversalResult, utc_now_iso


DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_NODES = 200
HARD_MAX_DEPTH = 4
HARD_MAX_NODES = 1000


RELATION_ENDPOINTS = {
    "parent": ("parents", "parent", "is_a_parent"),
    "child": ("children", "child", "is_a_child"),
    "descendant": ("descendants", "descendant", "is_a_descendant"),
    "role": ("roles", "role", "role"),
    "inverse_role": ("inverseRoles", "inverse_role", "inverse_role"),
    "association": ("associations", "association", "association"),
    "inverse_association": ("inverseAssociations", "inverse_association", "inverse_association"),
}


def clamp_limits(max_depth: int, max_nodes: int) -> Tuple[int, int]:
    if max_depth < 0:
        max_depth = 0
    if max_nodes < 1:
        max_nodes = 1
    return min(max_depth, HARD_MAX_DEPTH), min(max_nodes, HARD_MAX_NODES)


def _matches_filter(value: str, allowed: Optional[Set[str]]) -> bool:
    if not allowed:
        return True
    return value.lower() in allowed


def _relation_names(names: Optional[Iterable[str]]) -> Optional[Set[str]]:
    if not names:
        return None
    return {name.lower() for name in names}


def _edge_types(types: Optional[Iterable[str]]) -> Optional[Set[str]]:
    if not types:
        return None
    return {edge_type.lower() for edge_type in types}


def _node_from_raw(raw: Dict[str, object], release_version: str) -> TraversalNode:
    return TraversalNode(
        code=str(raw.get("code") or raw.get("relatedCode") or ""),
        preferred_name=str(raw.get("name") or raw.get("relatedName") or raw.get("label") or ""),
        terminology=str(raw.get("terminology") or "ncit"),
        release_version=str(raw.get("version") or release_version),
    )


def _edge_from_raw(
    source_code: str,
    raw: Dict[str, object],
    edge_type: str,
    default_relationship_name: str,
) -> Optional[TraversalEdge]:
    target_code = str(raw.get("relatedCode") or raw.get("code") or "")
    if not target_code:
        return None
    relationship_name = str(raw.get("type") or raw.get("association") or default_relationship_name)
    return TraversalEdge(
        source_code=source_code,
        target_code=target_code,
        edge_type=edge_type,
        relationship_name=relationship_name,
        target_name=str(raw.get("relatedName") or raw.get("name") or raw.get("label") or ""),
    )


def traverse_ncit(
    client: EVSClient,
    start_codes: List[str],
    release_version: str,
    direction: str = "out",
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    include_hierarchy: bool = True,
    include_roles: bool = True,
    include_associations: bool = True,
    relationship_names: Optional[List[str]] = None,
    edge_types: Optional[List[str]] = None,
) -> TraversalResult:
    depth_limit, node_limit = clamp_limits(max_depth, max_nodes)
    relation_filter = _relation_names(relationship_names)
    edge_type_filter = _edge_types(edge_types)
    endpoints = []
    if include_hierarchy:
        if direction in ("out", "both"):
            endpoints.extend(["child", "descendant"])
        if direction in ("in", "both"):
            endpoints.append("parent")
    if include_roles:
        if direction in ("out", "both"):
            endpoints.append("role")
        if direction in ("in", "both"):
            endpoints.append("inverse_role")
    if include_associations:
        if direction in ("out", "both"):
            endpoints.append("association")
        if direction in ("in", "both"):
            endpoints.append("inverse_association")

    nodes: Dict[str, TraversalNode] = {}
    edges: List[TraversalEdge] = []
    visited: Set[Tuple[str, int]] = set()
    queue = deque((code, 0) for code in start_codes)
    for code in start_codes:
        nodes[code] = TraversalNode(
            code=code,
            preferred_name="",
            terminology="ncit",
            release_version=release_version,
        )

    truncated = False
    while queue:
        code, depth = queue.popleft()
        if (code, depth) in visited:
            continue
        visited.add((code, depth))
        if depth >= depth_limit:
            continue
        for endpoint_key in endpoints:
            relation, edge_type, default_name = RELATION_ENDPOINTS[endpoint_key]
            if not _matches_filter(edge_type, edge_type_filter):
                continue
            related_items = client.get_related(code, relation)
            for raw in related_items:
                edge = _edge_from_raw(code, raw, edge_type, default_name)
                if not edge:
                    continue
                if not _matches_filter(edge.relationship_name, relation_filter):
                    continue
                edges.append(edge)
                if edge.target_code not in nodes:
                    if len(nodes) >= node_limit:
                        truncated = True
                        continue
                    nodes[edge.target_code] = _node_from_raw(raw, release_version)
                    queue.append((edge.target_code, depth + 1))

    return TraversalResult(
        start_codes=start_codes,
        release_version=release_version,
        nodes=list(nodes.values()),
        edges=edges,
        truncated=truncated,
        max_depth=depth_limit,
        max_nodes=node_limit,
        retrieved_at=utc_now_iso(),
    )
