"""Shared data models.

The MCP layer returns dictionaries produced from these dataclasses. Keeping the
core models dependency-free makes the indexing and EVS behavior easy to test
without importing FastMCP.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ReleaseInfo:
    terminology: str
    version: str
    date: Optional[str]
    name: str
    terminology_version: Optional[str]
    latest: bool
    monthly: bool
    weekly: bool
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_raw: bool = False) -> Dict[str, Any]:
        data = asdict(self)
        if not include_raw:
            data.pop("raw", None)
        return data


@dataclass(frozen=True)
class NcitConcept:
    code: str
    preferred_name: str
    source_vocabulary: str
    terminology: str
    release_version: str
    release_date: Optional[str]
    retrieved_at: str
    source: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_raw: bool = False) -> Dict[str, Any]:
        data = asdict(self)
        if not include_raw:
            data.pop("raw", None)
        return data


@dataclass(frozen=True)
class IndexManifest:
    terminology: str
    release_version: str
    release_date: Optional[str]
    embedding_provider: str
    embedding_model: str
    concept_count: int
    built_at: str
    index_path: str
    active: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SearchHit:
    concept: NcitConcept
    score: float
    rank: int
    score_components: Dict[str, float] = field(default_factory=dict)

    def to_dict(self, include_raw: bool = False) -> Dict[str, Any]:
        data = asdict(self)
        data["concept"] = self.concept.to_dict(include_raw=include_raw)
        return data


@dataclass(frozen=True)
class TraversalNode:
    code: str
    preferred_name: str
    terminology: str
    release_version: str
    source_vocabulary: str = "NCI Thesaurus"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TraversalEdge:
    source_code: str
    target_code: str
    edge_type: str
    relationship_name: str
    target_name: Optional[str] = None
    source_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TraversalResult:
    start_codes: List[str]
    release_version: str
    nodes: List[TraversalNode]
    edges: List[TraversalEdge]
    truncated: bool
    max_depth: int
    max_nodes: int
    retrieved_at: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["nodes"] = [node.to_dict() for node in self.nodes]
        data["edges"] = [edge.to_dict() for edge in self.edges]
        return data


@dataclass(frozen=True)
class CadsrStatus:
    state: str
    message: str
    reuse_targets: List[str]
    findings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
