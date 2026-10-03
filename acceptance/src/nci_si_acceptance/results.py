"""What the suite's tests read from a tool's result and from the upstream request log."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from nci_si_acceptance.spec import REQUIRED_TOOLS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from nci_si_acceptance.tools import Result


# The caDSR export folder's listing, and the export whose date is the registry's state.
EXPORT_LISTING = "recorded/cadsr-ftp/cde-xml-listing.json"
EXPORT = "releasedCDEsXML-OD.zip"


def export_date(listing: str) -> str:
    """The date the export folder's listing gives the export, ISO-8601."""

    (dated,) = re.findall(rf">{re.escape(EXPORT)}</a>\s+(\d{{4}}-\d{{2}}-\d{{2}})", listing)
    return dated


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
    """The release a call's items name: for a caDSR call, which pins no registry release, the
    registry alone (X-21); for any other, the fixture set's, which a cross-domain item names in
    release beside the registry state (provenance)."""

    if REQUIRED_TOOLS[name] == "cadsr":
        return "cadsr", None
    return pinned["terminology"], pinned["release"]


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


def sparql_rows(document: dict[str, Any]) -> list[dict[str, str]]:
    """The rows of a recorded SPARQL answer, each variable by its value."""

    bindings = document["response"]["body"]["results"]["bindings"]
    return [{key: value["value"] for key, value in row.items()} for row in bindings]


def bare_code(iri: str) -> str:
    """The code an NCIt IRI ends in: C4817 of ...Thesaurus.owl#C4817."""

    return iri.rpartition("#")[2]


def element_ids(uses: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """The data elements of data element uses, by public id and version."""

    return {(use["dataElement"]["publicId"], use["dataElement"]["version"]) for use in uses}


def value_ids(uses: list[dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    """Permissible value uses by data element, value and the concept it stands for."""

    return {
        (
            use["dataElement"]["publicId"],
            use["dataElement"]["version"],
            use["value"],
            use["conceptCode"],
        )
        for use in uses
    }
