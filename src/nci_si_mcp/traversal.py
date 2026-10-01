"""Breadth-first NCIt traversal bounded by depth, node count and edge count.

The walk reads live EVS, one batched concept request per level, so its cost
grows with the number of nodes expanded rather than with the number of edges.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .errors import InputValidationError
from .evs import EVSClient, EVSNotFoundError, EVSResponseError, EVSResponseTooLargeError
from .models import TraversalEdge, TraversalNode, TraversalResult, utc_now_iso

DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_NODES = 200
DEFAULT_MAX_EDGES = 1000
HARD_MAX_DEPTH = 4
HARD_MAX_NODES = 1000
HARD_MAX_EDGES = 5000
# Concepts per EVS request. Inverse relations of hub concepts run to megabytes
# each (3.4 MB for a subset concept that lists its members), so inward walks
# use small batches to stay under the response-size limit.
BATCH_SIZE = 50
INVERSE_BATCH_SIZE = 10

logger = logging.getLogger(__name__)

# Edge type -> (key of the relation list in an EVS concept payload, name given
# to edges that carry no relationship name of their own).
RELATIONS: Dict[str, Tuple[str, str]] = {
    "parent": ("parents", "is_a_parent"),
    "child": ("children", "is_a_child"),
    "descendant": ("descendants", "is_a_descendant"),
    "role": ("roles", "role"),
    "inverse_role": ("inverseRoles", "inverse_role"),
    "association": ("associations", "association"),
    "inverse_association": ("inverseAssociations", "inverse_association"),
}


def clamp_limits(max_depth: int, max_nodes: int) -> Tuple[int, int]:
    if max_depth < 0:
        max_depth = 0
    if max_nodes < 1:
        max_nodes = 1
    return min(max_depth, HARD_MAX_DEPTH), min(max_nodes, HARD_MAX_NODES)


def clamp_edge_limit(max_edges: int) -> int:
    if max_edges < 1:
        max_edges = 1
    return min(max_edges, HARD_MAX_EDGES)


def select_edge_types(
    direction: str,
    include_hierarchy: bool,
    include_roles: bool,
    include_associations: bool,
    edge_types: Optional[Iterable[str]],
) -> List[str]:
    """Resolve the direction, include flags and explicit edge types to follow.

    `descendant` is followed only when named in `edge_types`, because it
    repeats what `child` edges already reach within the depth limit.
    """

    outward = direction in ("out", "both")
    inward = direction in ("in", "both")
    available: List[str] = []
    if include_hierarchy:
        available += ["child", "descendant"] if outward else []
        available += ["parent"] if inward else []
    if include_roles:
        available += ["role"] if outward else []
        available += ["inverse_role"] if inward else []
    if include_associations:
        available += ["association"] if outward else []
        available += ["inverse_association"] if inward else []

    requested = list(edge_types or [])
    if requested:
        excluded = sorted(set(requested) - set(available))
        if excluded:
            raise InputValidationError(
                f"Edge types {', '.join(excluded)} are excluded by direction "
                f"'{direction}' and the include flags"
            )
        return [edge_type for edge_type in available if edge_type in requested]
    selected = [edge_type for edge_type in available if edge_type != "descendant"]
    if not selected:
        raise InputValidationError("The include flags leave no edge type to traverse")
    return selected


def _fetch_batch(
    client: EVSClient, batch: List[str], terminology: str, include: str
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Fetch one batch, halving it while the response is too large.

    Returns the concepts and the codes whose relations alone exceed the limit;
    those come back with their minimal payload only.
    """

    try:
        return client.get_concepts_by_codes(batch, terminology=terminology, include=include), []
    except EVSResponseTooLargeError:
        if len(batch) == 1:
            minimal = client.get_concepts_by_codes(batch, terminology=terminology, include="minimal")
            return minimal, list(batch)
    middle = len(batch) // 2
    left, left_oversized = _fetch_batch(client, batch[:middle], terminology, include)
    right, right_oversized = _fetch_batch(client, batch[middle:], terminology, include)
    return left + right, left_oversized + right_oversized


def _fetch_concepts(
    client: EVSClient,
    codes: List[str],
    terminology: str,
    include: str,
    release_version: str,
    batch_size: int,
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Fetch concepts with their relations and verify they belong to the release."""

    found: Dict[str, Dict[str, Any]] = {}
    oversized: List[str] = []
    for offset in range(0, len(codes), batch_size):
        concepts, too_large = _fetch_batch(
            client, codes[offset : offset + batch_size], terminology, include
        )
        found.update((str(raw.get("code") or ""), raw) for raw in concepts)
        oversized += too_large
    missing = [code for code in codes if code not in found]
    if missing:
        raise EVSNotFoundError(
            f"NCIt release {release_version} has no concept {', '.join(missing)}"
        )
    served = {str(raw.get("version") or "") for raw in found.values()}
    if served != {release_version}:
        raise EVSResponseError(
            f"EVS served release {', '.join(sorted(served))} for a request pinned to "
            f"{release_version}"
        )
    return found, oversized


def traverse_ncit(
    client: EVSClient,
    start_codes: List[str],
    release_version: str,
    terminology: str = "ncit",
    direction: str = "out",
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_edges: int = DEFAULT_MAX_EDGES,
    include_hierarchy: bool = True,
    include_roles: bool = True,
    include_associations: bool = True,
    relationship_names: Optional[List[str]] = None,
    edge_types: Optional[List[str]] = None,
) -> TraversalResult:
    """Walk outward from the start codes and return the nodes and edges reached.

    `terminology` is the EVS path segment; pass a release's pinned terminology
    so every request reads `release_version`, which is then verified on each
    fetched concept. A node at depth d is d hops from a start code.
    `descendant` edges run from a start code to every descendant within
    `max_depth` hierarchy levels and place the target at its level.

    An edge is emitted only when both of its nodes are. `truncated` reports
    that something was dropped: by the node or edge limit, or because the
    relations of a concept exceeded the EVS response-size limit. Nodes at the
    depth limit are not expanded, which is not truncation.
    """

    depth_limit, node_limit = clamp_limits(max_depth, max_nodes)
    edge_limit = clamp_edge_limit(max_edges)
    selected = select_edge_types(
        direction, include_hierarchy, include_roles, include_associations, edge_types
    )
    name_filter = {name.lower() for name in relationship_names or []}
    # Relations read from the concept payload; descendants have their own request.
    payload_keys = [RELATIONS[edge_type][0] for edge_type in selected if edge_type != "descendant"]
    include = ",".join(["minimal", *payload_keys])
    inward = any(edge_type.startswith("inverse") for edge_type in selected)
    batch_size = INVERSE_BATCH_SIZE if inward else BATCH_SIZE
    start = list(dict.fromkeys(start_codes))
    if len(start) > node_limit:
        raise InputValidationError("max_nodes must accommodate every start code")

    nodes: Dict[str, TraversalNode] = {}
    edges: List[TraversalEdge] = []
    seen_edges: Set[Tuple[str, str, str, str]] = set()
    truncated = False
    frontiers: Dict[int, List[str]] = {0: start}

    def result() -> TraversalResult:
        return TraversalResult(
            start_codes=start,
            release_version=release_version,
            nodes=list(nodes.values()),
            edges=edges,
            truncated=truncated,
            max_depth=depth_limit,
            max_nodes=node_limit,
            max_edges=edge_limit,
            retrieved_at=utc_now_iso(),
        )

    for depth in range(depth_limit + 1):
        frontier = frontiers.get(depth, [])
        expand = depth < depth_limit
        if not frontier or (depth > 0 and not (expand and payload_keys)):
            continue
        # Start codes are always fetched, to name them and to prove they exist.
        concepts, oversized = _fetch_concepts(
            client,
            frontier,
            terminology,
            include if expand else "minimal",
            release_version,
            batch_size,
        )
        if oversized:
            # Their relation lists are empty in `concepts`, so they add no edges.
            logger.warning("traverse_relations_too_large codes=%s", ",".join(oversized))
            truncated = True
        if depth == 0:
            for code in frontier:
                nodes[code] = TraversalNode(
                    code=code,
                    preferred_name=str(concepts[code].get("name") or ""),
                    terminology="ncit",
                    release_version=release_version,
                )
        if not expand:
            break
        for code in frontier:
            raw = concepts[code]
            for edge_type in selected:
                payload_key, default_name = RELATIONS[edge_type]
                if edge_type != "descendant":
                    items = raw.get(payload_key) or []
                elif depth == 0:
                    items = client.get_descendants(code, depth_limit, terminology=terminology)
                else:
                    continue
                for item in items:
                    target = str(item.get("relatedCode") or item.get("code") or "")
                    if not target:
                        continue
                    relationship_name = str(item.get("type") or default_name)
                    if name_filter and relationship_name.lower() not in name_filter:
                        continue
                    edge_key = (code, target, edge_type, relationship_name)
                    if edge_key in seen_edges:
                        continue
                    if target not in nodes and len(nodes) >= node_limit:
                        truncated = True
                        continue
                    if len(edges) >= edge_limit:
                        truncated = True
                        return result()
                    target_name = str(item.get("relatedName") or item.get("name") or "")
                    seen_edges.add(edge_key)
                    edges.append(
                        TraversalEdge(
                            source_code=code,
                            target_code=target,
                            edge_type=edge_type,
                            relationship_name=relationship_name,
                            target_name=target_name,
                            source_name=nodes[code].preferred_name,
                        )
                    )
                    if target not in nodes:
                        nodes[target] = TraversalNode(
                            code=target,
                            preferred_name=target_name,
                            terminology="ncit",
                            release_version=release_version,
                        )
                        hops = int(item.get("level") or 1) if edge_type == "descendant" else 1
                        frontiers.setdefault(depth + hops, []).append(target)
    return result()
