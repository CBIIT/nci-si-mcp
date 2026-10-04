"""The release model: which release a call reads, resolved once and threaded through it.

A `ReleaseContext` names the EVS release every request of one call is pinned to. It is resolved
by `resolve_evs_release` at the start of a call and never kept between calls, so a release that
EVS has since superseded is never served from memory (A3.1, A3.2). `registry_state` is the
caDSR counterpart: caDSR publishes no registry release, so the state is the export's date and
never an invented identifier (A3.8).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any

from .errors import PlatformError, with_next_step
from .evs import EVSClient

ITEM_VERSIONING = "per data element"


@dataclass(frozen=True, slots=True)
class ReleaseContext:
    """One release of an EVS terminology, as one call pins its requests to it."""

    terminology: str
    channel: str
    version: str
    date: str | None
    name: str
    # The path segment that addresses exactly this release, for example `ncit_26.09d`.
    pinned_terminology: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _not_available(message: str, requested: str) -> PlatformError:
    return PlatformError(
        "release_not_available",
        with_next_step(
            message,
            "Retry later, or set NCI_SI_RELEASE_CHANNEL to the other channel (monthly or weekly)",
        ),
        requested=requested,
        source="evs",
    )


def resolve_evs_release(evs: EVSClient, terminology: str, channel: str) -> ReleaseContext:
    """The release `channel` currently names for `terminology`, or `release_not_available`.

    `latest` is set per channel, so the query asks for the rows that are both latest and
    tagged with the channel and requires exactly one. Zero or several rows mean EVS does not
    name the release, and none is guessed.
    """

    rows = evs.get_terminologies(terminology, latest=True, tag=channel)
    requested = f"{terminology} {channel}"
    versions = [str(row.get("version") or "") for row in rows]
    if len(rows) != 1:
        # The error record has no key for the rows found, so `requested` names them too.
        found = ", ".join(versions) or "none"
        raise _not_available(
            f"EVS lists {len(rows)} {requested} releases as latest, not exactly one ({found})",
            f"{requested} (EVS lists: {found})",
        )
    (row,) = rows
    if not versions[0]:
        raise _not_available(f"The latest {requested} release has no version", requested)
    return ReleaseContext(
        terminology=terminology,
        channel=channel,
        version=versions[0],
        date=row.get("date"),
        name=str(row.get("name", "")),
        pinned_terminology=row.get("terminologyVersion") or f"{terminology}_{versions[0]}",
    )


@dataclass(frozen=True, slots=True)
class RegistryState:
    """The content state of the caDSR registry (A3.8).

    `registry_identifier` is None while caDSR publishes no registry release; `export_date` is
    the most specific provenance there is, None when the export carries no date.
    """

    registry_identifier: str | None
    export_date: str | None
    item_versioning: str = ITEM_VERSIONING

    def to_dict(self) -> dict[str, Any]:
        return {
            "registryIdentifier": self.registry_identifier,
            "exportDate": self.export_date,
            "itemVersioning": self.item_versioning,
        }


def _export_date(last_modified: str | None) -> str | None:
    """The ISO-8601 UTC time of an HTTP-date `Last-Modified` value; None when there is none."""

    if not last_modified:
        return None
    try:
        parsed = parsedate_to_datetime(last_modified)
    except TypeError, ValueError:
        raise PlatformError(
            "upstream_unavailable",
            with_next_step(
                f"The caDSR export's Last-Modified value {last_modified!r} is not an HTTP date",
                "Retry later; no export date is guessed",
            ),
            surface="cadsr",
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def registry_state(
    last_modified: str | None, upstream_identifier: str | None = None
) -> RegistryState:
    """The registry state from the export's `Last-Modified` header and any upstream identifier.

    No identifier is ever made up: it stays None until the registry publishes one. Once it
    does (C-1) the field is required, so a blank or non-text identifier fails closed with
    `release_not_available` instead of being passed on.
    """

    if upstream_identifier is not None and not (
        isinstance(upstream_identifier, str) and upstream_identifier.strip()
    ):
        raise PlatformError(
            "release_not_available",
            with_next_step(
                "The caDSR registry names a release identifier that is blank",
                "Retry later; no registry state is used without a usable identifier",
            ),
            requested="cadsr registry",
            source="cadsr",
        )
    return RegistryState(
        registry_identifier=upstream_identifier.strip() if upstream_identifier else None,
        export_date=_export_date(last_modified),
    )
