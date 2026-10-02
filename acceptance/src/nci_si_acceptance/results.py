"""What the suite's tests read from a tool's result and from the upstream request log."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from nci_si_acceptance.tools import Result


def error_code(result: Result) -> str | None:
    """The code of an error result that is one error record and nothing else, or None."""

    content = result.content
    record = content.get("error") if isinstance(content, dict) else None
    if not result.is_error or not isinstance(record, dict) or set(content) != {"error"}:
        return None
    return record.get("code")


def requests_naming(log: Iterable[dict[str, Any]], codes: Iterable[str]) -> list[dict[str, Any]]:
    """The upstream requests that name any of `codes`, in their path or their parameters."""

    wanted = set(codes)
    return [entry for entry in log if wanted & _words(entry)]


def _words(entry: dict[str, Any]) -> set[str]:
    """Every path segment and every comma-separated parameter value of a request."""

    values = [value for values in entry["params"].values() for value in values]
    return {word for text in [*entry["path"].split("/"), *values] for word in text.split(",")}
