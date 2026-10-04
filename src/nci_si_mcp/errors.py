"""Shared error types and serialization for CLI and MCP responses."""

from __future__ import annotations

from typing import Any, Literal

# The closed set of failure classes (A2.5). Every error record carries one of them.
ErrorClass = Literal[
    "invalid_request",
    "not_found",
    "release_unavailable",
    "upstream_unavailable",
    "bound_exceeded",
    "internal",
]


class PlatformError(Exception):
    """A failure to report to the caller: its class, a message that names the caller's
    next step, and optionally the data that step needs (`details`)."""

    def __init__(self, error_class: ErrorClass, message: str, /, **details: Any) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message
        self.details = details


class InputValidationError(ValueError):
    """Raised when a public API input is invalid."""


class IndexCompatibilityError(RuntimeError):
    """Raised when an index write or search would mix releases or embedding spaces,
    or when the database was written by a newer schema than this code supports."""


class IndexBuildError(RuntimeError):
    """Raised when the concepts handed to the index cannot form one release.

    The service rejects such EVS payloads before indexing, so this signals a
    bug in a caller rather than a condition to report to a user.
    """


class NoActiveIndexError(RuntimeError):
    """Raised when a search is attempted before any index has been built."""


class IndexStorageError(RuntimeError):
    """Raised when SQLite cannot open, read or write the index database."""


def with_next_step(message: str, step: str) -> str:
    """Append the caller's next step to a message."""

    return message.rstrip(".") + ". " + step


def serialise(error: PlatformError) -> dict[str, Any]:
    """Return the error record `{"error": {"code", "message", "details"?}}`.

    The one place a failure becomes a result. The record is the whole result: it
    has no other key, so it cannot be mistaken for an empty success. Argument
    parsing (argparse, MCP schema validation) and unexpected exceptions are
    reported by the CLI and MCP runtimes themselves and do not use it.
    """

    record: dict[str, Any] = {"code": error.error_class, "message": error.message}
    if error.details:
        record["details"] = error.details
    return {"error": record}


def is_error_record(result: dict[str, Any]) -> bool:
    """Whether a result is an error record, as `serialise` builds it."""

    return set(result) == {"error"}
