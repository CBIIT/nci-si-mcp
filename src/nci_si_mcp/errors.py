"""Shared error types and serialization for CLI and MCP responses."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal
from uuid import uuid4

# The closed set of error codes: the specification's error record (spec/records.yaml).
ErrorCode = Literal[
    "invalid_request",
    "permission_denied",
    "not_found",
    "release_not_available",
    "release_mismatch",
    "upstream_unavailable",
    "timeout",
    "bound_exceeded",
    "capability_unavailable",
    "cursor_expired",
    "internal_error",
]


class PlatformError(Exception):
    """A failure to report to the caller: its code, a message that names the caller's
    next step, and optionally the data that step needs (`details`)."""

    def __init__(self, code: ErrorCode, message: str, /, **details: Any) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.details = details


class InputValidationError(ValueError):
    """Raised when a public API input is invalid; `parameter` names the argument."""

    def __init__(self, message: str, /, parameter: str | None = None) -> None:
        super().__init__(message)
        self.details: dict[str, Any] = (
            {"parameter": parameter, "reason": message} if parameter else {}
        )


class IndexCompatibilityError(RuntimeError):
    """Raised when an index write or search would mix releases or embedding spaces,
    or when the database was written by a newer schema than this code supports."""


class IndexBuildError(RuntimeError):
    """Raised when the concepts handed to the index cannot form one release.

    The service rejects such EVS payloads before indexing, so this signals a
    bug in a caller rather than a condition to report to a user.
    """


class IndexEvaluationError(IndexBuildError):
    """An operator build violates the evaluation or sample-isolation policy."""


class IndexStateError(IndexBuildError):
    """An operator-selected build is unavailable or a concurrent writer changed it."""


class NoActiveIndexError(RuntimeError):
    """Raised when a search is attempted before any index has been built."""


class IndexStorageError(RuntimeError):
    """Raised when SQLite cannot open, read or write the index database."""


def with_next_step(message: str, step: str) -> str:
    """Append the caller's next step to a message."""

    return message.rstrip(".") + ". " + step


# The identifier of the call in progress, set once per call by its adapter.
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def current_correlation_id() -> str | None:
    """The correlation identifier of the call in progress, or None outside any call."""

    return _correlation_id.get()


def call_correlation_id() -> str:
    """The correlation identifier of the call in progress, for the provenance of its items.

    Every handler runs inside the audit correlation scope, so none means a bug.
    """

    value = current_correlation_id()
    if value is None:
        raise RuntimeError("An item's provenance was built outside any call")
    return value


@contextmanager
def correlated(correlation_id: object = None) -> Iterator[str]:
    """Run one call under its correlation identifier (M7.1).

    The caller's identifier is used when it is a non-empty string, otherwise one
    is generated. The audit boundary opens this once per call; `serialise` reads it.
    """

    value = correlation_id if isinstance(correlation_id, str) and correlation_id else uuid4().hex
    token = _correlation_id.set(value)
    try:
        yield value
    finally:
        _correlation_id.reset(token)


def serialise(error: PlatformError) -> dict[str, Any]:
    """Return the error record `{"error": {code, message, details?, correlationId}}`.

    The one place a failure becomes a result. The record is the whole result: it
    has no other key, so it cannot be mistaken for an empty success. The
    correlation identifier is the one of the call in progress; a failure raised
    outside any call (a direct service call) gets a fresh one. Argument parsing
    (argparse, MCP schema validation) and unexpected exceptions are reported by
    the CLI and MCP runtimes themselves and do not use it.
    """

    record: dict[str, Any] = {"code": error.code, "message": error.message}
    if error.details:
        record["details"] = error.details
    record["correlationId"] = current_correlation_id() or uuid4().hex
    return {"error": record}


def is_error_record(result: dict[str, Any]) -> bool:
    """Whether a result is an error record, as `serialise` builds it."""

    return set(result) == {"error"}
