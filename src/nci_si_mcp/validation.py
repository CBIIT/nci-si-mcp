"""Validation and normalization for public service inputs."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal, get_args

from .errors import InputValidationError
from .traversal import HARD_MAX_NODES

SearchMode = Literal["hybrid", "bm25", "vector"]
Direction = Literal["out", "in", "both"]
EdgeType = Literal[
    "parent",
    "child",
    "descendant",
    "role",
    "inverse_role",
    "association",
    "inverse_association",
]

NCIT_CODE_RE = re.compile(r"C[0-9]+")
SEARCH_MODES = frozenset(get_args(SearchMode))
TRAVERSAL_DIRECTIONS = frozenset(get_args(Direction))
TRAVERSAL_EDGE_TYPES = frozenset(get_args(EdgeType))
MAX_SEARCH_LIMIT = 100


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_ncit_code(code: str) -> str:
    normalized = str(code or "").strip().upper()
    if not NCIT_CODE_RE.fullmatch(normalized):
        raise InputValidationError(
            f"NCIt code must have the form C followed by digits, not {str(code)[:40]!r}"
        )
    return normalized


def validate_ncit_codes(codes: Iterable[str]) -> list[str]:
    normalized = list(dict.fromkeys(validate_ncit_code(code) for code in codes))
    if not normalized:
        raise InputValidationError("At least one NCIt code is required")
    return normalized


def validate_search(query: str, limit: int, mode: str) -> tuple[str, int, str]:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise InputValidationError("Search query must not be blank")
    if not _is_int(limit) or not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise InputValidationError(f"Search limit must be between 1 and {MAX_SEARCH_LIMIT}")
    normalized_mode = str(mode or "").lower()
    if normalized_mode not in SEARCH_MODES:
        allowed = ", ".join(sorted(SEARCH_MODES))
        raise InputValidationError(f"Search mode must be one of: {allowed}")
    return normalized_query, limit, normalized_mode


def _validate_limits(codes: list[str], max_depth: int, max_nodes: int, max_edges: int) -> None:
    if not _is_int(max_depth) or max_depth < 0:
        raise InputValidationError("max_depth must be a non-negative integer")
    for field, value in (("max_nodes", max_nodes), ("max_edges", max_edges)):
        if not _is_int(value) or value < 1:
            raise InputValidationError(f"{field} must be a positive integer")
    node_limit = min(max_nodes, HARD_MAX_NODES)
    if len(codes) > node_limit:
        raise InputValidationError(
            f"{len(codes)} start codes exceed the node limit of {node_limit} "
            f"(max_nodes, at most {HARD_MAX_NODES})"
        )


def _normalize_edge_types(edge_types: Iterable[str] | None) -> list[str] | None:
    if not edge_types:
        return None
    normalized = list(
        dict.fromkeys(str(edge_type or "").strip().lower() for edge_type in edge_types)
    )
    if not TRAVERSAL_EDGE_TYPES.issuperset(normalized):
        allowed = ", ".join(sorted(TRAVERSAL_EDGE_TYPES))
        raise InputValidationError(f"Edge type must be one of: {allowed}")
    return normalized


def _normalize_relationship_names(names: Iterable[str] | None) -> list[str] | None:
    if not names:
        return None
    normalized = list(dict.fromkeys(str(name or "").strip() for name in names))
    if "" in normalized:
        raise InputValidationError("Relationship names must not be blank")
    return normalized


def validate_traversal(
    start_codes: Iterable[str],
    direction: str,
    max_depth: int,
    max_nodes: int,
    max_edges: int,
    edge_types: Iterable[str] | None,
    relationship_names: Iterable[str] | None = None,
) -> tuple[list[str], str, list[str] | None, list[str] | None]:
    codes = validate_ncit_codes(start_codes)
    normalized_direction = str(direction or "").lower()
    if normalized_direction not in TRAVERSAL_DIRECTIONS:
        allowed = ", ".join(sorted(TRAVERSAL_DIRECTIONS))
        raise InputValidationError(f"Traversal direction must be one of: {allowed}")
    _validate_limits(codes, max_depth, max_nodes, max_edges)
    return (
        codes,
        normalized_direction,
        _normalize_edge_types(edge_types),
        _normalize_relationship_names(relationship_names),
    )
