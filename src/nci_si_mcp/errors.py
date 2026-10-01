"""Shared error types and serialization for CLI and MCP responses."""

from __future__ import annotations

from typing import Any, Dict, Literal

ErrorCode = Literal[
    "invalid_request",
    "invalid_configuration",
    "startup_failed",
    "concept_not_found",
    "concepts_missing",
    "release_unresolved",
    "evs_unavailable",
    "evs_invalid_response",
    "version_mismatch",
    "no_active_index",
    "index_incompatible",
    "index_build_failed",
    "index_storage_error",
    "release_not_active",
    "index_not_active",
]


class InputValidationError(ValueError):
    """Raised when a public API input is invalid."""


class IndexCompatibilityError(RuntimeError):
    """Raised when an index write or search would mix releases or embedding spaces."""


class IndexBuildError(RuntimeError):
    """Raised when the concepts supplied for an index build cannot form one release."""


class NoActiveIndexError(RuntimeError):
    """Raised when a search is attempted before any index has been built."""


def error_response(code: ErrorCode, message: str, /, **details: Any) -> Dict[str, Any]:
    """Return the error envelope for failures that the service and adapters handle.

    Argument parsing (argparse, MCP schema validation) and unexpected exceptions
    are reported by the CLI and MCP runtimes themselves and do not use it.
    """

    response: Dict[str, Any] = {
        "isError": True,
        "error": code,
        "message": message,
    }
    if details:
        response["details"] = details
    return response
