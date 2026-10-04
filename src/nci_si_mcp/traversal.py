"""Breadth-first NCIt traversal bounded by depth, node count and edge count.

The walk reads live EVS level by level in batched concept requests, so its cost
grows with the number of nodes expanded rather than with the number of edges.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from itertools import batched
from typing import Any

from .errors import InputValidationError, call_correlation_id
from .evs import (
    EVSClient,
    EVSNotFoundError,
    EVSResponseError,
    EVSResponseTooLargeError,
    concept_path,
    object_list,
    verify_release,
)
from .models import (
    TraversalEdge,
    TraversalNode,
    TraversalProvenance,
    TraversalResult,
    Truncation,
    release_ref,
    upstream_origin,
    utc_now_iso,
)
from .release import ReleaseContext

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
# NCIt's exclusion relationships are exactly these eight roles. An assertion is negative by its
# relationship's code, never by its name (A5.7); `spec/records.yaml` is the source.
NCIT_EXCLUSION_CODES = frozenset(f"R{number}" for number in range(135, 143))


def clamp_limits(max_depth: int, max_nodes: int) -> tuple[int, int]:
    max_depth = max(max_depth, 0)
    max_nodes = max(max_nodes, 1)
    return min(max_depth, HARD_MAX_DEPTH), min(max_nodes, HARD_MAX_NODES)


def clamp_edge_limit(max_edges: int) -> int:
    max_edges = max(max_edges, 1)
    return min(max_edges, HARD_MAX_EDGES)


# Edge types in the order they are followed, each with the direction and the
# include flag that make it available.
_EDGE_TYPE_RULES = (
    ("child", "out", "hierarchy"),
    ("descendant", "out", "hierarchy"),
    ("parent", "in", "hierarchy"),
    ("role", "out", "roles"),
    ("inverse_role", "in", "roles"),
    ("association", "out", "associations"),
    ("inverse_association", "in", "associations"),
)


# The direction in which each edge type is followed, which the provenance of an edge states.
EDGE_DIRECTIONS = {edge_type: way for edge_type, way, _ in _EDGE_TYPE_RULES}


def _available_edge_types(direction: str, included: dict[str, bool]) -> list[str]:
    return [
        edge_type
        for edge_type, way, group in _EDGE_TYPE_RULES
        if included[group] and direction in (way, "both")
    ]


def _default_edge_types(available: list[str]) -> list[str]:
    selected = [edge_type for edge_type in available if edge_type != "descendant"]
    if not selected:
        raise InputValidationError(
            "The include flags leave no edge type to traverse", "include_hierarchy"
        )
    return selected


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

    included = {
        "hierarchy": include_hierarchy,
        "roles": include_roles,
        "associations": include_associations,
    }
    available = _available_edge_types(direction, included)
    requested = set(edge_types or [])
    if not requested:
        return _default_edge_types(available)
    excluded = sorted(requested - set(available))
    if excluded:
        raise InputValidationError(
            f"Edge types {', '.join(excluded)} are excluded by direction "
            f"'{direction}' and the include flags",
            "edge_types",
        )
    return [edge_type for edge_type in available if edge_type in requested]


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
            minimal = client.get_concepts_by_codes(
                batch, terminology=terminology, include="minimal"
            )
            return minimal, list(batch)
    middle = len(batch) // 2
    left, left_oversized = _fetch_batch(client, batch[:middle], terminology, include)
    right, right_oversized = _fetch_batch(client, batch[middle:], terminology, include)
    return left + right, left_oversized + right_oversized


def _fetch_concepts(
    client: EVSClient,
    codes: list[str],
    release: ReleaseContext,
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


def _relationship(edge_type: str, item: dict[str, Any]) -> dict[str, str]:
    """The relationship that brought a relation item in: its kind, and for a role or an
    association its code and name. A hierarchy link has neither, and none is invented."""

    if edge_type in HIERARCHY_EDGE_TYPES:
        return {"kind": edge_type}
    relationship = {"kind": "role" if "role" in edge_type else "association"}
    named = {"code": item.get("code"), "name": item.get("type")}
    return relationship | {key: str(value) for key, value in named.items() if value}


def _polarity(relationship: dict[str, str]) -> str:
    return "negative" if relationship.get("code") in NCIT_EXCLUSION_CODES else "positive"


def _edge(
    code: str,
    source_name: str,
    edge_type: str,
    item: dict[str, Any],
    provenance: TraversalProvenance,
) -> TraversalEdge:
    """Build the edge that a relation item of concept `code` stands for."""

    # A role or association item names its target in `relatedCode`; its own
    # `code` is that of the relationship.
    hierarchy = edge_type in HIERARCHY_EDGE_TYPES
    target_key, name_key = ("code", "name") if hierarchy else ("relatedCode", "relatedName")
    target = str(item.get(target_key) or "")
    if not target:
        raise EVSResponseError(
            f"EVS returned a {edge_type} relation of {code} without a {target_key}"
        )
    return TraversalEdge(
        source_code=code,
        target_code=target,
        edge_type=edge_type,
        relationship_name=str(item.get("type") or RELATIONS[edge_type][1]),
        provenance=provenance,
        target_name=str(item.get(name_key) or ""),
        source_name=source_name,
    )


def _missing_concepts(release: ReleaseContext, missing: list[str], depth: int) -> Exception:
    codes = ", ".join(missing)
    if depth == 0:
        return EVSNotFoundError(
            f"NCIt release {release.version} has no concept {codes}", identifiers=missing
        )
    return EVSResponseError(
        f"EVS relations in release {release.version} refer to {codes}, which it does not serve"
    )


@dataclass
class _Walk:
    """One traversal in progress: its limits and what it has emitted so far."""

    client: EVSClient
    release: ReleaseContext
    edge_types: list[str]
    depth_limit: int
    node_limit: int
    edge_limit: int
    name_filter: set[str]
    retrieved_at: str = field(default_factory=utc_now_iso)
    correlation_id: str = field(default_factory=call_correlation_id)
    nodes: dict[str, TraversalNode] = field(default_factory=dict)
    edges: list[TraversalEdge] = field(default_factory=list)
    seen_edges: set[tuple[str, str, str, str]] = field(default_factory=set)
    unexpanded: list[str] = field(default_factory=list)
    # What a limit left out: the concepts the node limit dropped, the edges the edge limit
    # dropped, and the first bound that dropped anything.
    dropped_nodes: set[str] = field(default_factory=set)
    dropped_edges: set[tuple[str, str, str, str]] = field(default_factory=set)
    first_bound: str | None = None
    # Descendants of the start codes by level; level n is emitted with the
    # other edges that reach depth n.
    descendants: dict[int, list[tuple[str, dict[str, Any]]]] = field(default_factory=dict)
    # Edge types whose relations come with the concept payload; descendants
    # have their own request.
    payload_types: list[str] = field(init=False)
    batch_size: int = field(init=False)

    def __post_init__(self) -> None:
        self.payload_types = [
            edge_type for edge_type in self.edge_types if edge_type != "descendant"
        ]
        inverse = any(edge_type.startswith("inverse") for edge_type in self.edge_types)
        self.batch_size = INVERSE_BATCH_SIZE if inverse else BATCH_SIZE

    def _provenance(
        self,
        depth: int,
        uri: str,
        edge_type: str | None = None,
        item: dict[str, Any] | None = None,
        upstream: dict[str, Any] | None = None,
    ) -> TraversalProvenance:
        """The provenance of an item at `depth`, read from `uri`; `edge_type` and `item` name
        the relation that brought it in, and the concept asked about has none."""

        relationship = _relationship(edge_type, item or {}) if edge_type else None
        return TraversalProvenance(
            release=release_ref(self.release.terminology, self.release.version, self.release.date),
            source="evs_rest",
            served_by="live",
            retrieved_at=self.retrieved_at,
            correlation_id=self.correlation_id,
            source_uri=uri,
            upstream=upstream,
            depth=depth,
            relationship=relationship,
            direction=EDGE_DIRECTIONS[edge_type] if edge_type else None,
            polarity=_polarity(relationship) if relationship else None,
        )

    def _concept_uri(self, code: str) -> str:
        return self.client.uri(concept_path(self.release.pinned_terminology, code))

    def _node(self, code: str, name: str, provenance: TraversalProvenance) -> TraversalNode:
        return TraversalNode(
            code=code,
            preferred_name=name,
            terminology="ncit",
            provenance=provenance,
        )

    def _bound_reached(self, bound: str) -> None:
        self.first_bound = self.first_bound or bound

    def _mark_unexpanded(self, codes: Iterable[str]) -> None:
        self.unexpanded += [code for code in codes if code not in self.unexpanded]
        self._bound_reached("upstream_cap")

    def truncation(self) -> Truncation:
        """The record of the first bound that dropped anything, counting what it dropped.

        The concepts and relations beyond a dropped one were never read, so the count is a
        lower bound, and `exact` is False.
        """

        if self.first_bound is None:
            return Truncation(occurred=False)
        limit, reached, omitted = {
            "nodes": (self.node_limit, len(self.nodes), len(self.dropped_nodes)),
            "edges": (self.edge_limit, len(self.edges), len(self.dropped_edges)),
            "upstream_cap": (
                self.client.max_response_bytes,
                self.client.max_response_bytes,
                len(self.unexpanded),
            ),
        }[self.first_bound]
        return Truncation(
            occurred=True,
            bound=self.first_bound,
            limit=limit,
            reached=reached,
            omitted=omitted,
            exact=False,
        )

    def _needs_fetch(self, frontier: list[str], depth: int) -> bool:
        # Start codes are always fetched, to name them and to prove they exist.
        expand = depth < self.depth_limit
        return depth == 0 or bool(expand and self.payload_types and frontier)

    def fetch(self, frontier: list[str], depth: int) -> dict[str, dict[str, Any]]:
        """Read the concepts of the frontier, with the relations to follow from them."""

        if not self._needs_fetch(frontier, depth):
            return {}
        relations = [RELATIONS[edge_type][0] for edge_type in self.payload_types]
        include = ",".join(["minimal", *relations]) if depth < self.depth_limit else "minimal"
        concepts, missing, oversized = _fetch_concepts(
            self.client, frontier, self.release, include, self.batch_size
        )
        if missing:
            raise _missing_concepts(self.release, missing, depth)
        if oversized:
            # Their relation lists are empty in `concepts`, so they add no edges.
            logger.warning(
                "traverse_relations_too_large codes=%s limit=NCI_SI_EVS_MAX_RESPONSE_BYTES",
                ",".join(oversized),
            )
            self._mark_unexpanded(oversized)
        return concepts

    def start(self, start_codes: list[str], concepts: dict[str, dict[str, Any]]) -> None:
        """Emit the start nodes and read their descendants when those are followed."""

        for code in start_codes:
            raw = concepts[code]
            # A start code was read in full, so EVS said where it comes from; a node reached
            # through a relation list was named there and nothing more.
            provenance = self._provenance(0, self._concept_uri(code), upstream=upstream_origin(raw))
            self.nodes[code] = self._node(code, str(raw.get("name") or ""), provenance)
        if self.depth_limit > 0 and "descendant" in self.edge_types:
            self._read_descendants(start_codes)

    def _read_descendants(self, start_codes: list[str]) -> None:
        for code in start_codes:
            try:
                items = self.client.get_descendants(
                    code, self.depth_limit, terminology=self.release.pinned_terminology
                )
            except EVSResponseTooLargeError as exc:
                logger.warning("traverse_descendants_too_large code=%s reason=%s", code, exc)
                self._mark_unexpanded([code])
                continue
            for item in items:
                level = _level(item, self.depth_limit)
                self.descendants.setdefault(level, []).append((code, item))

    def _found(
        self, frontier: list[str], concepts: dict[str, dict[str, Any]], depth: int
    ) -> list[tuple[str, str, dict[str, Any]]]:
        """The relation items that lead from the frontier to depth + 1."""

        found = [
            (code, edge_type, item)
            for code in frontier
            for edge_type in self.payload_types
            for item in object_list(concepts[code], RELATIONS[edge_type][0])
        ]
        return found + [
            (code, "descendant", item) for code, item in self.descendants.get(depth + 1, [])
        ]

    def _skips(self, edge: TraversalEdge, key: tuple[str, str, str, str]) -> bool:
        """Whether the name filter excludes the edge or it was emitted before."""

        filtered = self.name_filter and edge.relationship_name.lower() not in self.name_filter
        return bool(filtered) or key in self.seen_edges

    def _add(self, edge: TraversalEdge) -> bool:
        """Emit the edge unless a filter or a limit drops it; whether it reached a new node."""

        key = (edge.source_code, edge.target_code, edge.edge_type, edge.relationship_name)
        if self._skips(edge, key):
            return False
        new_node = edge.target_code not in self.nodes
        if new_node and len(self.nodes) >= self.node_limit:
            self.dropped_nodes.add(edge.target_code)
            self._bound_reached("nodes")
            return False
        if len(self.edges) >= self.edge_limit:
            self.dropped_edges.add(key)
            self._bound_reached("edges")
            return False
        self.seen_edges.add(key)
        self.edges.append(edge)
        if new_node:
            held = replace(edge.provenance, source_uri=self._concept_uri(edge.target_code))
            self.nodes[edge.target_code] = self._node(edge.target_code, edge.target_name, held)
        return new_node

    def _edge_uri(self, code: str, edge_type: str) -> str:
        """The URL that holds the relation: the concept's, or its descendants'."""

        uri = self._concept_uri(code)
        return f"{uri}/descendants" if edge_type == "descendant" else uri

    def follow(
        self, frontier: list[str], concepts: dict[str, dict[str, Any]], depth: int
    ) -> list[str]:
        """Emit the edges that leave the frontier and return the nodes they newly reach."""

        reached: list[str] = []
        for code, edge_type, item in self._found(frontier, concepts, depth):
            provenance = self._provenance(
                depth + 1, self._edge_uri(code, edge_type), edge_type, item
            )
            edge = _edge(code, self.nodes[code].preferred_name, edge_type, item, provenance)
            if self._add(edge):
                reached.append(edge.target_code)
        return reached

    def result(self, start_codes: list[str]) -> TraversalResult:
        return TraversalResult(
            start_codes=start_codes,
            nodes=list(self.nodes.values()),
            edges=self.edges,
            truncation=self.truncation(),
            max_depth=self.depth_limit,
            max_nodes=self.node_limit,
            max_edges=self.edge_limit,
        )


def traverse_ncit(
    client: EVSClient,
    start_codes: list[str],
    release: ReleaseContext,
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

    An edge is emitted only when both of its nodes are. The truncation record
    names the first bound that dropped something: the node or edge limit, or
    the EVS response-size limit, which leaves the relations or descendants of
    a concept unread. Nodes at the depth limit are not expanded, which is not
    truncation.
    """

    depth_limit, node_limit = clamp_limits(max_depth, max_nodes)
    walk = _Walk(
        client=client,
        release=release,
        edge_types=edge_types,
        depth_limit=depth_limit,
        node_limit=node_limit,
        edge_limit=clamp_edge_limit(max_edges),
        name_filter={name.lower() for name in relationship_names or []},
    )
    frontier = start_codes
    concepts = walk.fetch(frontier, 0)
    walk.start(start_codes, concepts)
    for depth in range(depth_limit):
        frontier = walk.follow(frontier, concepts, depth)
        if walk.dropped_edges:
            break
        concepts = walk.fetch(frontier, depth + 1)
    return walk.result(start_codes)
