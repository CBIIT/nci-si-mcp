"""Serialized tool records, distinct from the index's stored dataclasses.

These dependency-free declarations describe the existing wire names and omitted keys.
The MCP adapter generates their JSON schemas using the SDK's schema machinery.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from .errors import ErrorCode
from .validation import (
    EdgeType,
    Polarity,
    ProvenanceSource,
    RelationshipKind,
    ReleaseChannel,
    SearchMode,
    ServedBy,
    TruncationBound,
)


class ErrorRecord(TypedDict):
    code: ErrorCode
    message: str
    correlationId: str
    details: NotRequired[dict[str, Any]]


class ErrorResult(TypedDict):
    error: ErrorRecord


class ReleaseReference(TypedDict):
    terminology: str
    identifier: str
    date: NotRequired[str]


class Provenance(TypedDict):
    release: ReleaseReference
    source: ProvenanceSource
    servedBy: ServedBy
    retrievedAt: str
    correlationId: str
    sourceUri: NotRequired[str]
    upstream: NotRequired[dict[str, Any]]
    attribution: NotRequired[str]


class Relationship(TypedDict):
    kind: str
    code: NotRequired[str]
    name: NotRequired[str]


class TraversalProvenance(Provenance):
    depth: int
    relationship: NotRequired[Relationship]
    direction: NotRequired[Literal["in", "out"]]
    polarity: NotRequired[Polarity]
    qualifiers: NotRequired[Any]
    evidence: NotRequired[Any]


class Untruncated(TypedDict):
    occurred: Literal[False]


class Truncated(TypedDict):
    occurred: Literal[True]
    bound: TruncationBound
    limit: int
    reached: int
    omitted: int
    exact: bool
    perKind: NotRequired[dict[str, Truncation]]


type Truncation = Untruncated | Truncated


class Fallback(TypedDict):
    reason: Literal["upstream_unavailable"]
    message: str


class ConceptResult(TypedDict):
    code: str
    preferred_name: str
    source_vocabulary: str
    terminology: str
    evidence: dict[str, Any]
    provenance: Provenance
    fallback: NotRequired[Fallback]


class Concept(TypedDict):
    code: str
    terminology: str
    name: str
    active: bool
    status: NotRequired[str]
    provenance: Provenance
    synonyms: NotRequired[list[dict[str, Any]]]
    definitions: NotRequired[list[dict[str, Any]]]
    properties: NotRequired[list[dict[str, Any]]]
    semanticType: NotRequired[list[str]]


class ConceptBatch(TypedDict):
    concepts: list[Concept]
    missing: list[str]
    provenance: NotRequired[Provenance]


class Replacement(TypedDict):
    code: str
    terminology: str
    name: str
    provenance: Provenance


class RetiredCode(TypedDict):
    code: str
    terminology: str
    active: bool
    status: NotRequired[str]
    replacements: list[Replacement]
    provenance: Provenance


class Subset(TypedDict):
    code: str
    terminology: str
    name: str
    provenance: Provenance


class SubsetsResult(TypedDict):
    subsets: list[Subset]
    provenance: NotRequired[Provenance]


class ValueSetMember(TypedDict):
    code: str
    terminology: str
    name: str
    inactive: NotRequired[Literal[True]]
    provenance: Provenance


class ValueSetExpansion(TypedDict):
    members: list[ValueSetMember]
    total: int
    truncation: Truncation
    provenance: NotRequired[Provenance]


class Mapping(TypedDict):
    targetCode: str
    targetTerminology: str
    targetName: str
    type: str
    targetTermType: NotRequired[str]
    targetTerminologyVersion: NotRequired[str]
    provenance: Provenance


class MappingsResult(TypedDict):
    mappings: list[Mapping]
    provenance: NotRequired[Provenance]


class CatalogueRelationship(TypedDict):
    code: str
    terminology: str
    name: str
    kind: RelationshipKind
    polarity: Polarity
    provenance: Provenance


class RelationshipsResult(TypedDict):
    relationships: list[CatalogueRelationship]
    provenance: NotRequired[Provenance]


class RankedConcept(TypedDict):
    concept: Concept
    score: NotRequired[float]
    matchedOn: NotRequired[str]


class ConceptSearch(TypedDict):
    results: list[RankedConcept]
    totalKnown: int
    nextCursor: NotRequired[str]
    provenance: NotRequired[Provenance]


class Node(TypedDict):
    code: str
    terminology: str
    name: str
    active: bool
    status: NotRequired[str]
    provenance: TraversalProvenance


class Edge(TypedDict):
    sourceCode: str
    sourceTerminology: str
    targetCode: str
    targetTerminology: str
    provenance: TraversalProvenance


class Hierarchy(TypedDict):
    nodes: list[Node]
    truncation: Truncation
    provenance: NotRequired[Provenance]
    paths: NotRequired[list[list[str]]]
    nextCursor: NotRequired[str]


class Neighborhood(Hierarchy):
    edges: list[Edge]


class SearchHit(TypedDict):
    concept: ConceptResult
    score: float
    rank: int
    score_components: dict[str, float]


class SearchResult(TypedDict):
    query: str
    mode: SearchMode
    hits: list[SearchHit]
    truncation: Truncation
    provenance: NotRequired[Provenance]


class TraversalNode(TypedDict):
    code: str
    preferred_name: str
    terminology: str
    source_vocabulary: str
    provenance: TraversalProvenance


class TraversalEdge(TypedDict):
    source_code: str
    target_code: str
    edge_type: EdgeType
    relationship_name: NotRequired[str]
    target_name: str
    source_name: str
    provenance: TraversalProvenance


class TraversalResult(TypedDict):
    start_codes: list[str]
    nodes: list[TraversalNode]
    edges: list[TraversalEdge]
    truncation: Truncation
    max_depth: int
    max_nodes: int
    max_edges: int


class SelectedRelease(TypedDict):
    terminology: str
    channel: ReleaseChannel
    version: str
    date: str | None


class ResolvedReleaseResult(SelectedRelease):
    alternatives: list[str]
    provenance: Provenance


class TerminologyResult(TypedDict):
    terminology: str
    release: str
    provenance: Provenance


class TerminologiesResult(TypedDict):
    terminologies: list[TerminologyResult]


class IndexManifestResult(TypedDict):
    terminology: str
    version: str
    concepts: int
    embedding: dict[str, Any]
    builtAt: str
    provenance: Provenance


class EmbeddingStatus(TypedDict):
    provider: str
    model: str
    active_index_compatible: bool


class ReleaseResult(TypedDict):
    # EVS owns the API-version payload; its fields pass through without reinterpretation.
    evs_api: dict[str, Any]
    selected_release: SelectedRelease | ErrorResult
    active_index: IndexManifestResult | None
    embedding: EmbeddingStatus
    provenance: NotRequired[Provenance]
