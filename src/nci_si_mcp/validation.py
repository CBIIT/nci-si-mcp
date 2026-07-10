"""Validation and normalization for public service inputs."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

from .errors import InputValidationError

NCIT_CODE_RE = re.compile(r"^C[0-9]+$", re.IGNORECASE)
SEARCH_MODES = frozenset({"bm25", "vector", "hybrid"})
TRAVERSAL_DIRECTIONS = frozenset({"in", "out", "both"})
TRAVERSAL_EDGE_TYPES = frozenset(
    {
        "parent",
        "child",
        "descendant",
        "role",
        "inverse_role",
        "association",
        "inverse_association",
    }
)
MAX_SEARCH_LIMIT = 100


def validate_ncit_code(code: str) -> str:
    normalized = str(code or "").strip().upper()
    if not NCIT_CODE_RE.fullmatch(normalized):
        raise InputValidationError("NCIt code must have the form C followed by digits")
    return normalized


def validate_ncit_codes(codes: Iterable[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for code in codes:
        value = validate_ncit_code(code)
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    if not normalized:
        raise InputValidationError("At least one NCIt code is required")
    return normalized


def validate_search(query: str, limit: int, mode: str) -> Tuple[str, int, str]:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise InputValidationError("Search query must not be blank")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_SEARCH_LIMIT:
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
) -> Tuple[List[str], str, Optional[List[str]]]:
    codes = validate_ncit_codes(start_codes)
    normalized_direction = str(direction or "").lower()
    if normalized_direction not in TRAVERSAL_DIRECTIONS:
        allowed = ", ".join(sorted(TRAVERSAL_DIRECTIONS))
        raise InputValidationError(f"Traversal direction must be one of: {allowed}")
    for field, value in (
        ("max_depth", max_depth),
        ("max_nodes", max_nodes),
        ("max_edges", max_edges),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise InputValidationError(f"{field} must be a non-negative integer")
    if max_nodes < 1 or max_edges < 1:
        raise InputValidationError("max_nodes and max_edges must be at least 1")

    normalized_edge_types: Optional[List[str]] = None
    if edge_types:
        normalized_edge_types = []
        for edge_type in edge_types:
            normalized_edge_type = str(edge_type or "").strip().lower()
            if normalized_edge_type not in TRAVERSAL_EDGE_TYPES:
                allowed = ", ".join(sorted(TRAVERSAL_EDGE_TYPES))
                raise InputValidationError(f"Edge type must be one of: {allowed}")
            if normalized_edge_type not in normalized_edge_types:
                normalized_edge_types.append(normalized_edge_type)
    return codes, normalized_direction, normalized_edge_types
