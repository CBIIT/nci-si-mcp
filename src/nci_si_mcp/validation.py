"""Validation and normalization for public service inputs."""

from __future__ import annotations

import re
from typing import Iterable, List, Literal, Optional, Tuple, get_args

from .errors import InputValidationError

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
        raise InputValidationError("NCIt code must have the form C followed by digits")
    return normalized


def validate_ncit_codes(codes: Iterable[str]) -> List[str]:
    normalized = list(dict.fromkeys(validate_ncit_code(code) for code in codes))
    if not normalized:
        raise InputValidationError("At least one NCIt code is required")
    return normalized


def validate_search(query: str, limit: int, mode: str) -> Tuple[str, int, str]:
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


def validate_traversal(
    start_codes: Iterable[str],
    direction: str,
    max_depth: int,
    max_nodes: int,
    max_edges: int,
    edge_types: Optional[Iterable[str]],
    relationship_names: Optional[Iterable[str]] = None,
) -> Tuple[List[str], str, Optional[List[str]], Optional[List[str]]]:
    codes = validate_ncit_codes(start_codes)
    normalized_direction = str(direction or "").lower()
    if normalized_direction not in TRAVERSAL_DIRECTIONS:
        allowed = ", ".join(sorted(TRAVERSAL_DIRECTIONS))
        raise InputValidationError(f"Traversal direction must be one of: {allowed}")
    if not _is_int(max_depth) or max_depth < 0:
        raise InputValidationError("max_depth must be a non-negative integer")
    for field, value in (("max_nodes", max_nodes), ("max_edges", max_edges)):
        if not _is_int(value) or value < 1:
            raise InputValidationError(f"{field} must be a positive integer")

    normalized_edge_types: Optional[List[str]] = None
    if edge_types:
        normalized_edge_types = list(
            dict.fromkeys(str(edge_type or "").strip().lower() for edge_type in edge_types)
        )
        if not TRAVERSAL_EDGE_TYPES.issuperset(normalized_edge_types):
            allowed = ", ".join(sorted(TRAVERSAL_EDGE_TYPES))
            raise InputValidationError(f"Edge type must be one of: {allowed}")

    normalized_names: Optional[List[str]] = None
    if relationship_names:
        normalized_names = list(
            dict.fromkeys(str(name or "").strip() for name in relationship_names)
        )
        if "" in normalized_names:
            raise InputValidationError("Relationship names must not be blank")
    return codes, normalized_direction, normalized_edge_types, normalized_names
