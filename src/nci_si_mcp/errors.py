"""Shared error types and serialization for CLI and MCP responses."""

from __future__ import annotations

from typing import Any, Dict


class InputValidationError(ValueError):
    """Raised when a public API input is invalid."""


class IndexCompatibilityError(RuntimeError):
    """Raised when an index cannot be used with the configured runtime."""


def error_response(code: str, message: str, **details: Any) -> Dict[str, Any]:
    """Return the single error envelope used by service, CLI, and MCP layers."""

    response: Dict[str, Any] = {
        "isError": True,
        "error": code,
        "message": message,
    }
    if details:
        response["details"] = details
    return response
