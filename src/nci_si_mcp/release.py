"""The release model: which release a call reads, resolved once and threaded through it.

A `ReleaseContext` names the EVS release every request of one call is pinned to. It is resolved
by `resolve_evs_release` at the start of a call and never kept between calls, so a release that
EVS has since superseded is never served from memory (A3.1, A3.2). `registry_state` is the
caDSR counterpart: caDSR publishes no registry release, so the state is the export's date and
never an invented identifier (A3.8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from .errors import PlatformError, with_next_step
from .evs import EVSClient


@dataclass(frozen=True, slots=True)
class ReleaseContext:
    """One release of an EVS terminology, as one call pins its requests to it."""

    terminology: str
    channel: str
    version: str
    date: str | None
    # The path segment that addresses exactly this release, for example `ncit_26.09d`.
    pinned_terminology: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "terminology": self.terminology,
            "channel": self.channel,
            "version": self.version,
            "date": self.date,
        }


def _not_available(message: str, requested: str, found: list[str] | None = None) -> PlatformError:
    return PlatformError(
        "release_not_available",
        with_next_step(
            message,
            "Retry later, or set NCI_SI_RELEASE_CHANNEL to the other channel (monthly or weekly)",
        ),
        requested=requested,
        source="evs",
        **({"found": found} if found else {}),
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
        found = ", ".join(versions) or "none"
        raise _not_available(
            f"EVS lists {len(rows)} {requested} releases as latest, not exactly one ({found})",
            requested,
            versions,
        )
    (row,) = rows
    if not versions[0]:
        raise _not_available(f"The latest {requested} release has no version", requested)
    return ReleaseContext(
        terminology=terminology,
        channel=channel,
        version=versions[0],
        date=row.get("date"),
        pinned_terminology=row.get("terminologyVersion") or f"{terminology}_{versions[0]}",
    )


@dataclass(frozen=True, slots=True)
class RegistryState:
    """The content state of the caDSR registry (A3.8).

    The identifier is absent while caDSR publishes no registry release; the export's date
    is then the most specific provenance available, not a reproducible registry release.
    """

    identifier: str | None
    generated_at: str
    source_distribution: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "published": self.identifier is not None,
            "generatedAt": self.generated_at,
            "sourceDistribution": self.source_distribution,
            **({"identifier": self.identifier} if self.identifier is not None else {}),
        }


class RegistryMetadataError(ValueError):
    """The registry supplied unusable release metadata, not a missing requested release."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.details = {"surface": "cadsr"}


def _generation_date(value: str | None, published: bool) -> str:
    """Validate a published ISO date, or convert the export's HTTP date to UTC."""

    if not isinstance(value, str) or not value.strip():
        raise RegistryMetadataError("The caDSR generation date is missing or not text")
    try:
        if published:
            datetime.fromisoformat(value)
            return value
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()
    except TypeError, ValueError, OverflowError:
        raise RegistryMetadataError("The caDSR generation date is invalid") from None


def registry_state(
    generation_date: str | None,
    upstream_identifier: str | None = None,
    *,
    source_distribution: str,
) -> RegistryState:
    """The registry state from upstream metadata, never an invented identifier or date.

    Without an identifier, `generation_date` is the export's `Last-Modified` HTTP date.
    With one, it is that release's own ISO-8601 date, passed through unchanged. The caller
    names the distribution the date came from (`releasedCDEsXML-OD.zip` today).
    """

    if upstream_identifier is not None and not (
        isinstance(upstream_identifier, str) and upstream_identifier.strip()
    ):
        raise RegistryMetadataError("The caDSR registry release identifier is blank or not text")
    if not isinstance(source_distribution, str) or not source_distribution.strip():
        raise RegistryMetadataError("The caDSR source distribution is missing or not text")
    return RegistryState(
        identifier=upstream_identifier,
        generated_at=_generation_date(generation_date, upstream_identifier is not None),
        source_distribution=source_distribution,
    )
