"""Serialized tool records, distinct from the index's stored dataclasses.

These dependency-free declarations describe the existing wire names and omitted keys.
The MCP adapter generates their JSON schemas using the SDK's schema machinery.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from .errors import ErrorCode
from .validation import (
    DataElementConceptRole,
    DatasetName,
    EdgeType,
    MatchedItemType,
    Polarity,
    ProvenanceSource,
    RelationshipKind,
    ReleaseChannel,
    SearchMode,
    ServedBy,
    StoredValueConfidence,
    TruncationBound,
)


class ErrorRecord(TypedDict):
    code: ErrorCode
    message: str
    correlationId: str
    details: NotRequired[dict[str, Any]]


class ErrorResult(TypedDict):
    error: ErrorRecord


class CodeMapValue(TypedDict):
    value: str
    conceptCode: NotRequired[str]


class CodeMapIdentity(TypedDict):
    publicId: str
    version: str


class CodeMap(TypedDict):
    dataElement: CodeMapIdentity
    crdcName: str | None
    usedBy: list[str]
    valueLevelBinding: bool
    coverage: int
    values: list[CodeMapValue]
    provenance: Provenance


class CodeMapsResult(TypedDict):
    codeMaps: list[CodeMap]
    nextCursor: NotRequired[str]
    provenance: NotRequired[Provenance]


class CodeMapResource(CodeMapsResult):
    truncation: NotRequired[Truncation]


class DataElementMatch(TypedDict):
    entity: str
    dataElement: DataElement
    score: int | float
    rule: str
    matchedText: str


class DataElementMatches(TypedDict):
    matches: list[DataElementMatch]
    provenance: NotRequired[Provenance]


class MatchedRegistryItem(TypedDict):
    itemType: MatchedItemType
    publicId: str
    version: str
    name: str
    concept: NotRequired[str]
    evsSource: NotRequired[str]
    context: str
    workflowStatus: str
    registrationStatus: NotRequired[str]
    provenance: Provenance


class MatchCrosswalk(TypedDict):
    code: str
    description: str


class ValueMeaningMatch(TypedDict):
    item: MatchedRegistryItem
    rule: str
    score: NotRequired[int | float]
    crosswalk: NotRequired[MatchCrosswalk]


class ValueMeaningMatches(TypedDict):
    matches: list[ValueMeaningMatch]
    provenance: NotRequired[Provenance]


class ReleaseReference(TypedDict):
    terminology: str
    identifier: str
    date: NotRequired[str]


class RegistryReference(TypedDict):
    registry: Literal["cadsr"]
    identifier: NotRequired[str]
    date: NotRequired[str]


class Provenance(TypedDict):
    release: ReleaseReference | RegistryReference
    source: ProvenanceSource
    servedBy: ServedBy
    retrievedAt: str
    correlationId: str
    sourceUri: NotRequired[str]
    upstream: NotRequired[dict[str, Any]]
    attribution: NotRequired[str]
    registry: NotRequired[RegistryReference]
    graphs: NotRequired[list[GraphReference]]


class GraphReference(TypedDict):
    graph: str
    date: str
    version: NotRequired[str]


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


class RegistryReleaseResult(TypedDict):
    published: bool
    identifier: NotRequired[str]
    generatedAt: str
    sourceDistribution: str
    provenance: NotRequired[Provenance]


class RegistryItemIdentity(TypedDict):
    publicId: str
    version: str
    longName: str | None


class ValueMeaningConcept(TypedDict):
    conceptCode: str
    longName: str | None
    primary: bool


class ValueMeaning(RegistryItemIdentity):
    concepts: list[ValueMeaningConcept]


class PermissibleValue(TypedDict):
    publicId: str
    value: str
    valueMeaning: ValueMeaning
    provenance: Provenance


class ClassificationScheme(RegistryItemIdentity):
    context: str | None
    items: list[RegistryItemIdentity]
    provenance: Provenance


class ConceptAssociation(TypedDict):
    conceptCode: str
    longName: str | None
    role: DataElementConceptRole


class Form(RegistryItemIdentity):
    context: str | None
    workflowStatus: str | None
    registrationStatus: str | None
    dateCreated: str | None
    dateModified: str | None
    provenance: Provenance
    modules: NotRequired[list[dict[str, Any]]]


class DataElement(RegistryItemIdentity):
    context: str | None
    workflowStatus: str | None
    registrationStatus: str | None
    dateCreated: str | None
    dateModified: str | None
    provenance: Provenance
    permissibleValues: NotRequired[list[PermissibleValue]]
    valueDomain: NotRequired[dict[str, Any]]
    conceptAssociations: NotRequired[list[ConceptAssociation]]
    alternateNames: NotRequired[list[dict[str, Any]]]
    classificationSchemes: NotRequired[list[ClassificationScheme]]


class DataElementHit(TypedDict):
    dataElement: DataElement
    score: NotRequired[float]
    matchedOn: NotRequired[str]


class DataElementSearch(TypedDict):
    results: list[DataElementHit]
    nextCursor: NotRequired[str]
    totalKnown: NotRequired[int]
    truncation: Truncation
    provenance: NotRequired[Provenance]


class RegistryContext(TypedDict):
    name: str
    provenance: Provenance


class ContextsResult(TypedDict):
    contexts: list[RegistryContext]
    nextCursor: NotRequired[str]
    provenance: NotRequired[Provenance]


class ClassificationSchemesResult(TypedDict):
    classificationSchemes: list[ClassificationScheme]
    nextCursor: NotRequired[str]
    provenance: NotRequired[Provenance]


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


class DataElementUse(TypedDict):
    dataElement: RegistryItemIdentity
    provenance: Provenance


class ValueUse(TypedDict):
    dataElement: CodeMapIdentity
    value: str
    conceptCode: str
    conceptTerminology: Literal["ncit"]
    provenance: Provenance


class DataElementUses(TypedDict):
    dataElements: list[DataElementUse]
    permissibleValues: NotRequired[list[ValueUse]]
    truncation: Truncation
    nextCursor: NotRequired[str]
    provenance: NotRequired[Provenance]


class PermissibleValueIdentity(TypedDict):
    dataElement: CodeMapIdentity
    value: str


class PermissibleValueConcept(Concept):
    permissibleValue: PermissibleValueIdentity


class MapsetSource(TypedDict):
    mapset: str
    version: str


class CrosswalkSource(TypedDict):
    crosswalk: Literal["CRDC"]
    dataElement: CodeMapIdentity


class StoredValue(TypedDict):
    value: str
    field: str
    source: MapsetSource | CrosswalkSource
    provenance: Provenance


class StoredValueEvidence(TypedDict):
    sources: list[MapsetSource | CrosswalkSource]
    valueLevelBinding: bool
    coverage: int


class StoredValuesResult(TypedDict):
    storedValues: list[StoredValue]
    confidence: StoredValueConfidence
    evidence: StoredValueEvidence
    provenance: NotRequired[Provenance]


class DatasetRelease(TypedDict):
    name: DatasetName
    version: NotRequired[str]
    date: str
    provenance: Provenance


class ReleaseAlignment(TypedDict):
    datasets: list[DatasetRelease]
    intervalDays: int
    warning: NotRequired[str]


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
