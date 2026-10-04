"""Shared data models.

The MCP layer returns dictionaries produced from these dataclasses. Keeping the
core models dependency-free makes the indexing and EVS behavior easy to test
without importing the optional `mcp` package.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from typing import Any

from .errors import call_correlation_id


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    terminology: str
    version: str
    date: str | None
    name: str
    terminology_version: str | None
    latest: bool
    monthly: bool
    weekly: bool

    @property
    def pinned_terminology(self) -> str:
        """EVS path segment that pins a request to exactly this release."""

        return self.terminology_version or f"{self.terminology}_{self.version}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProvenanceEnvelope:
    """The provenance record of one returned item (A4.4), field for field.

    `source_uri` is None only for a result that holds no item and so was produced from no
    upstream URL; `upstream` holds what the platform supplied about the item's origin, unchanged,
    and is left out when it supplied nothing (A4.3).
    """

    release: dict[str, str]
    source: str
    served_by: str
    retrieved_at: str
    correlation_id: str
    source_uri: str | None = None
    upstream: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "release": self.release,
            "source": self.source,
            "servedBy": self.served_by,
            "retrievedAt": self.retrieved_at,
            "correlationId": self.correlation_id,
        }
        if self.source_uri:
            data["sourceUri"] = self.source_uri
        if self.upstream:
            data["upstream"] = self.upstream
        return data


@dataclass(frozen=True, slots=True, kw_only=True)
class TraversalProvenance(ProvenanceEnvelope):
    """The provenance of an item reached by traversal (A4.2).

    The concept asked about has depth 0 and no relationship, direction or polarity.
    """

    depth: int
    relationship: dict[str, str] | None = None
    direction: str | None = None
    polarity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["depth"] = self.depth
        how_reached = {
            "relationship": self.relationship,
            "direction": self.direction,
            "polarity": self.polarity,
        }
        return data | {name: value for name, value in how_reached.items() if value}


@dataclass(frozen=True, slots=True)
class Truncation:
    """The truncation record of a bounded result (A5.4).

    When a bound was reached `omitted` is always a number, and `exact` is False where it is
    only a lower bound or an estimate.
    """

    occurred: bool
    bound: str | None = None
    limit: int | None = None
    reached: int | None = None
    omitted: int | None = None
    exact: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: value for key, value in data.items() if value is not None}


# The surface and the way of serving that each stored `source` stands for.
_ORIGINS = {"live_evs": ("evs_rest", "live"), "active_cache": ("evs_index", "index")}


def release_ref(terminology: str, identifier: str, date: str | None) -> dict[str, str]:
    """The terminology form of the provenance record's release; a date EVS gave none is left out."""

    ref = {"terminology": terminology, "identifier": identifier}
    return ref | {"date": date} if date else ref


def upstream_origin(raw: dict[str, Any]) -> dict[str, Any]:
    """What EVS REST says of an item's origin, under its own names: its terminology and version."""

    return {key: raw[key] for key in ("terminology", "version") if key in raw}


# NcitConcept and IndexManifest are stored as JSON in the index. A new field
# needs a default, or a schema migration that rewrites the stored payloads.
@dataclass(frozen=True, slots=True)
class NcitConcept:
    code: str
    preferred_name: str
    source_vocabulary: str
    terminology: str
    release_version: str
    release_date: str | None
    retrieved_at: str
    source: str
    evidence: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_stored(self) -> dict[str, Any]:
        return asdict(self)

    def provenance(self, source_uri: str) -> ProvenanceEnvelope:
        surface, served_by = _ORIGINS[self.source]
        return ProvenanceEnvelope(
            release=release_ref(self.terminology, self.release_version, self.release_date),
            source=surface,
            served_by=served_by,
            retrieved_at=self.retrieved_at,
            correlation_id=call_correlation_id(),
            source_uri=source_uri,
            upstream=upstream_origin(self.raw),
        )

    def to_dict(self, source_uri: str, include_raw: bool = False) -> dict[str, Any]:
        """The concept as an item of a result: its provenance replaces the release fields.

        The full EVS payload is added only when `include_raw` asks for it, which the CLI does.
        """

        data = {
            "code": self.code,
            "preferred_name": self.preferred_name,
            "source_vocabulary": self.source_vocabulary,
            "terminology": self.terminology,
            "evidence": self.evidence,
            "provenance": self.provenance(source_uri).to_dict(),
        }
        return data | {"raw": self.raw} if include_raw else data


@dataclass(frozen=True, slots=True)
class IndexManifest:
    terminology: str
    release_version: str
    release_date: str | None
    embedding_provider: str
    embedding_model: str
    concept_count: int
    built_at: str
    index_path: str
    embedding_dimensions: int | None = None
    active: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> IndexManifest:
        """Rebuild a stored manifest, ignoring keys this version does not know."""

        known = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in payload.items() if key in known})

    def embedding_matches(self, provider: str, model: str, dimensions: int | None = None) -> bool:
        """Whether vectors from this provider share the index's embedding space.

        Dimensions are compared only when both sides know them; an index built
        before dimensions were recorded stores None.
        """

        return (
            self.embedding_provider == provider
            and self.embedding_model == model
            and (
                dimensions is None
                or self.embedding_dimensions is None
                or self.embedding_dimensions == dimensions
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_result(self) -> dict[str, Any]:
        """The manifest as the index_manifest record: its fields and its provenance."""

        return self.to_dict() | {"provenance": self.provenance().to_dict()}

    def provenance(self) -> ProvenanceEnvelope:
        """The provenance of what the index serves: the release it holds, as built."""

        return ProvenanceEnvelope(
            release=release_ref(self.terminology, self.release_version, self.release_date),
            source="evs_index",
            served_by="index",
            retrieved_at=self.built_at,
            correlation_id=call_correlation_id(),
        )


@dataclass(frozen=True, slots=True)
class SearchHit:
    concept: NcitConcept
    score: float
    rank: int
    score_components: dict[str, float] = field(default_factory=dict)

    def to_dict(self, source_uri: str, include_raw: bool = False) -> dict[str, Any]:
        data = asdict(self)
        data["concept"] = self.concept.to_dict(source_uri, include_raw=include_raw)
        return data


def _with_provenance(item: Any) -> dict[str, Any]:
    """The fields of a traversal item, with its provenance as the record names its fields."""

    data = {f.name: getattr(item, f.name) for f in fields(item) if f.name != "provenance"}
    return data | {"provenance": item.provenance.to_dict()}


@dataclass(frozen=True, slots=True)
class TraversalNode:
    code: str
    preferred_name: str
    terminology: str
    provenance: TraversalProvenance
    source_vocabulary: str = "NCI Thesaurus"

    def to_dict(self) -> dict[str, Any]:
        return _with_provenance(self)


@dataclass(frozen=True, slots=True)
class TraversalEdge:
    source_code: str
    target_code: str
    edge_type: str
    relationship_name: str
    provenance: TraversalProvenance
    target_name: str = ""
    source_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _with_provenance(self)


@dataclass(frozen=True, slots=True)
class TraversalResult:
    start_codes: list[str]
    nodes: list[TraversalNode]
    edges: list[TraversalEdge]
    truncation: Truncation
    max_depth: int
    max_nodes: int
    max_edges: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["nodes"] = [node.to_dict() for node in self.nodes]
        data["edges"] = [edge.to_dict() for edge in self.edges]
        data["truncation"] = self.truncation.to_dict()
        return data


@dataclass(frozen=True, slots=True)
class CadsrStatus:
    state: str
    message: str
    reuse_targets: list[str]
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
