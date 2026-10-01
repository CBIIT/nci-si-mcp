"""Breadth-first NCIt traversal bounded by depth, node count and edge count.

The walk reads live EVS level by level in batched concept requests, so its cost
grows with the number of nodes expanded rather than with the number of edges.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from itertools import batched
from typing import Any

from .errors import InputValidationError
from .evs import (
    EVSClient,
    EVSNotFoundError,
    EVSResponseError,
    EVSResponseTooLargeError,
    object_list,
    verify_release,
)
from .models import ReleaseInfo, TraversalEdge, TraversalNode, TraversalResult, utc_now_iso

DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_NODES = 200
DEFAULT_MAX_EDGES = 1000
HARD_MAX_DEPTH = 4
HARD_MAX_NODES = 1000
HARD_MAX_EDGES = 5000
# Concepts per EVS request. Inverse relations of hub concepts run to megabytes
# each (3.4 MB for the subset concept C116977 in release 26.09d), so walks that
# follow inverse roles or inverse associations use small batches to stay under
# the response-size limit.
BATCH_SIZE = 50
INVERSE_BATCH_SIZE = 10

logger = logging.getLogger(__name__)

# Edge type -> (key of the relation list in an EVS concept payload, name given
# to edges that carry no relationship name of their own).
RELATIONS: dict[str, tuple[str, str]] = {
    "parent": ("parents", "is_a_parent"),
    "child": ("children", "is_a_child"),
    "descendant": ("descendants", "is_a_descendant"),
    "role": ("roles", "role"),
    "inverse_role": ("inverseRoles", "inverse_role"),
    "association": ("associations", "association"),
    "inverse_association": ("inverseAssociations", "inverse_association"),
}
HIERARCHY_EDGE_TYPES = frozenset({"parent", "child", "descendant"})


def clamp_limits(max_depth: int, max_nodes: int) -> tuple[int, int]:
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
    edge_types: Iterable[str] | None,
) -> list[str]:
    """Resolve the direction, include flags and explicit edge types to follow.

    `descendant` is followed only when named in `edge_types`, because `child`
    edges already reach those concepts within the depth limit.
    """

    outward = direction in ("out", "both")
    inward = direction in ("in", "both")
    available: list[str] = []
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
    client: EVSClient, batch: list[str], terminology: str, include: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch one batch, halving it while the response is too large.

    Returns the concepts and the codes whose relations alone exceed the limit;
    those come back with their minimal payload only.
    """

    try:
        return client.get_concepts_by_codes(batch, terminology=terminology, include=include), []
    except EVSResponseTooLargeError as exc:
        logger.info("traverse_batch_too_large concepts=%s reason=%s", len(batch), exc)
        if len(batch) == 1:
            minimal = client.get_concepts_by_codes(batch, terminology=terminology, include="minimal")
            return minimal, list(batch)
    middle = len(batch) // 2
    left, left_oversized = _fetch_batch(client, batch[:middle], terminology, include)
    right, right_oversized = _fetch_batch(client, batch[middle:], terminology, include)
    return left + right, left_oversized + right_oversized


def _fetch_concepts(
    client: EVSClient,
    codes: list[str],
    release: ReleaseInfo,
    include: str,
    batch_size: int,
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    """Fetch concepts with their relations, pinned to and verified against the release.

    Returns the payloads by code, the codes EVS did not return, and the codes
    whose relations were too large to read.
    """

    terminology = release.pinned_terminology
    found: dict[str, dict[str, Any]] = {}
    oversized: list[str] = []
    for batch in batched(codes, batch_size, strict=False):
        concepts, too_large = _fetch_batch(client, list(batch), terminology, include)
        found.update((str(raw.get("code") or ""), raw) for raw in concepts)
        oversized += too_large
    verify_release(found.values(), release.version)
    return found, [code for code in codes if code not in found], oversized


def _level(item: dict[str, Any], depth_limit: int) -> int:
    level = item.get("level")
    if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= depth_limit:
        raise EVSResponseError(
            f"EVS returned a descendant at level {level!r}; expected 1 to {depth_limit}"
        )
    return level


def traverse_ncit(
    client: EVSClient,
    start_codes: list[str],
    release: ReleaseInfo,
    edge_types: list[str],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_edges: int = DEFAULT_MAX_EDGES,
    relationship_names: list[str] | None = None,
) -> TraversalResult:
    """Walk breadth-first from the start codes along the given edge types.

    `start_codes` is the output of `validate_traversal`, which makes them
    distinct and fit the node limit, and `edge_types` is the output of
    `select_edge_types`. Every request is pinned to `release`, and each
    fetched concept is verified against it.

    The walk proceeds one depth at a time over all start codes together, so
    nearer nodes claim the node and edge limits before farther ones. A
    `descendant` edge runs from a start code to a descendant that EVS places
    within `max_depth` hierarchy levels, and reaches as deep as that level.
    EVS gives a descendant one level, which can exceed its shortest path.

    An edge is emitted only when both of its nodes are. `truncated` reports
    that something was dropped: by the node or edge limit, or because the
    relations or descendants of a concept exceeded the EVS response-size
    limit, in which case the concept is listed in `unexpanded_codes`. Nodes at
    the depth limit are not expanded, which is not truncation.
    """

    depth_limit, node_limit = clamp_limits(max_depth, max_nodes)
    edge_limit = clamp_edge_limit(max_edges)
    name_filter = {name.lower() for name in relationship_names or []}
    # Edge types whose relations come with the concept payload; descendants
    # have their own request.
    payload_types = [edge_type for edge_type in edge_types if edge_type != "descendant"]
    include = ",".join(["minimal", *(RELATIONS[edge_type][0] for edge_type in payload_types)])
    inverse = any(edge_type.startswith("inverse") for edge_type in edge_types)
    batch_size = INVERSE_BATCH_SIZE if inverse else BATCH_SIZE

    nodes: dict[str, TraversalNode] = {}
    edges: list[TraversalEdge] = []
    seen_edges: set[tuple[str, str, str, str]] = set()
    unexpanded: list[str] = []
    truncated = False
    # Descendants of the start codes by level; level n is emitted with the
    # other edges that reach depth n.
    descendants: dict[int, list[tuple[str, dict[str, Any]]]] = {}

    def result() -> TraversalResult:
        return TraversalResult(
            start_codes=start_codes,
            release_version=release.version,
            nodes=list(nodes.values()),
            edges=edges,
            truncated=truncated,
            max_depth=depth_limit,
            max_nodes=node_limit,
            max_edges=edge_limit,
            retrieved_at=utc_now_iso(),
            unexpanded_codes=unexpanded,
        )

    frontier = start_codes
    for depth in range(depth_limit + 1):
        expand = depth < depth_limit
        concepts: dict[str, dict[str, Any]] = {}
        # Start codes are always fetched, to name them and to prove they exist.
        if depth == 0 or (expand and payload_types and frontier):
            concepts, missing, oversized = _fetch_concepts(
                client, frontier, release, include if expand else "minimal", batch_size
            )
            if missing and depth == 0:
                raise EVSNotFoundError(
                    f"NCIt release {release.version} has no concept {', '.join(missing)}"
                )
            if missing:
                raise EVSResponseError(
                    f"EVS relations in release {release.version} refer to {', '.join(missing)}, "
                    "which it does not serve"
                )
            if oversized:
                # Their relation lists are empty in `concepts`, so they add no edges.
                logger.warning(
                    "traverse_relations_too_large codes=%s limit=NCI_SI_EVS_MAX_RESPONSE_BYTES",
                    ",".join(oversized),
                )
                unexpanded += oversized
                truncated = True
        if depth == 0:
            for code in start_codes:
                nodes[code] = TraversalNode(
                    code=code,
                    preferred_name=str(concepts[code].get("name") or ""),
                    terminology="ncit",
                    release_version=release.version,
                )
            if expand and "descendant" in edge_types:
                for code in start_codes:
                    try:
                        items = client.get_descendants(
                            code, depth_limit, terminology=release.pinned_terminology
                        )
                    except EVSResponseTooLargeError as exc:
                        logger.warning(
                            "traverse_descendants_too_large code=%s reason=%s", code, exc
                        )
                        if code not in unexpanded:
                            unexpanded.append(code)
                        truncated = True
                        continue
                    for item in items:
                        descendants.setdefault(_level(item, depth_limit), []).append((code, item))
        if not expand:
            break

        # Every edge found here leads to depth + 1.
        found = [
            (code, edge_type, item)
            for code in frontier
            for edge_type in payload_types
            for item in object_list(concepts[code], RELATIONS[edge_type][0])
        ]
        found += [(code, "descendant", item) for code, item in descendants.get(depth + 1, [])]
        frontier = []
        for code, edge_type, item in found:
            # A role or association item names its target in `relatedCode`; its
            # own `code` is that of the relationship.
            hierarchy = edge_type in HIERARCHY_EDGE_TYPES
            target_key = "code" if hierarchy else "relatedCode"
            target = str(item.get(target_key) or "")
            if not target:
                raise EVSResponseError(
                    f"EVS returned a {edge_type} relation of {code} without a {target_key}"
                )
            relationship_name = str(item.get("type") or RELATIONS[edge_type][1])
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
            target_name = str(item.get("name" if hierarchy else "relatedName") or "")
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
                    release_version=release.version,
                )
                frontier.append(target)
    return result()
