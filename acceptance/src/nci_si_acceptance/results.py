"""What the suite's tests read from a tool's result and from the upstream request log."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nci_si_acceptance.spec import parameters

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


def _provenance_of(item: Any) -> dict[str, Any]:
    return (item.get("provenance") or {}) if isinstance(item, dict) else {}


def release_of(item: Any) -> tuple[Any, Any]:
    """The terminology or registry and the release an item's provenance names."""

    release = _provenance_of(item).get("release") or {}
    return release.get("terminology", release.get("registry")), release.get("identifier")


def pinned_release(name: str, pinned: dict[str, str]) -> tuple[str, str | None]:
    """The release a call's items name: the fixture set's, for a tool that takes a release; for
    a caDSR call, which pins no registry release, the registry alone (X-21)."""

    if "release" in parameters(name)[0]:
        return pinned["terminology"], pinned["release"]
    return "cadsr", None


def identity(item: Any) -> Any:
    """An item by what it is: a concept by terminology and code, an edge by its ends and its
    relationship's code, a caDSR item by its public id and version (a code map by its data
    element's), a context by its name."""

    if not isinstance(item, dict):
        return item
    if "sourceCode" in item:
        relationship = (item.get("provenance") or {}).get("relationship") or {}
        return item.get("sourceCode"), item.get("targetCode"), relationship.get("code")
    if "code" not in item and "terminology" not in item:
        return _registry_identity(item)
    return item.get("terminology"), item.get("code")


def _registry_identity(item: dict[str, Any]) -> Any:
    """A caDSR item by its public id and version, a code map by its data element's, a context
    by its name."""

    element = item.get("dataElement")
    owner: dict[str, Any] = element if isinstance(element, dict) else item
    if "publicId" in owner:
        return owner.get("publicId"), owner.get("version")
    return item.get("name")
