"""Application service used by the CLI and MCP entrypoint."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .cadsr import CadsrAdapter
from .config import Settings
from .embeddings import create_embedding_provider
from .evs import EVSClient, EVSError, normalize_concept
from .index import LocalIndex
from .models import utc_now_iso
from .traversal import DEFAULT_MAX_DEPTH, DEFAULT_MAX_NODES, traverse_ncit


class NCISIService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.evs = EVSClient(settings.evs_base_url, settings.timeout_seconds)
        self.index = LocalIndex(settings.data_dir)
        self.embedding_provider = create_embedding_provider(
            settings.embedding_provider, settings.embedding_model
        )
        self.cadsr = CadsrAdapter()

    def release_info(self) -> Dict[str, Any]:
        evs_version: Dict[str, Any]
        try:
            evs_version = self.evs.get_api_version()
        except EVSError as exc:
            evs_version = {"isError": True, "message": str(exc)}
        selected = self.evs.resolve_monthly_ncit_release().to_dict()
        manifest = self.index.get_active_manifest()
        return {
            "evs_api": evs_version,
            "selected_monthly_release": selected,
            "active_index": manifest.to_dict() if manifest else None,
            "embedding": {
                "provider": self.embedding_provider.name,
                "model": self.embedding_provider.model,
            },
            "retrieved_at": utc_now_iso(),
        }

    def index_codes(self, codes: Iterable[str]) -> Dict[str, Any]:
        release = self.evs.resolve_monthly_ncit_release()
        raw_concepts = self.evs.get_concepts_by_codes(codes)
        manifest = self.index.upsert_concepts(
            raw_concepts=raw_concepts,
            release_date=release.date,
            embedding_provider=self.embedding_provider,
        )
        if manifest.release_version != release.version:
            raise RuntimeError(
                "EVS concept payload release did not match selected monthly release: "
                f"{manifest.release_version} != {release.version}"
            )
        return manifest.to_dict()

    def search(
        self,
        query: str,
        limit: int = 10,
        mode: str = "hybrid",
        include_raw: bool = False,
    ) -> Dict[str, Any]:
        hits = self.index.search(query, self.embedding_provider, limit=limit, mode=mode)
        manifest = self.index.get_active_manifest()
        return {
            "query": query,
            "mode": mode,
            "release_version": manifest.release_version if manifest else None,
            "hits": [hit.to_dict(include_raw=include_raw) for hit in hits],
            "retrieved_at": utc_now_iso(),
        }

    def lookup(self, code: str, live_only: bool = False, include_raw: bool = False) -> Dict[str, Any]:
        manifest = self.index.get_active_manifest()
        try:
            release = self.evs.resolve_monthly_ncit_release()
        except EVSError as exc:
            if live_only:
                return {"isError": True, "error": "evs_unavailable", "message": str(exc)}
            cached = self.index.get_concept(code, manifest.release_version if manifest else None)
            if cached:
                return cached.to_dict(include_raw=include_raw)
            return {
                "isError": True,
                "error": "evs_unavailable_no_active_cache",
                "message": str(exc),
            }

        try:
            raw = self.evs.get_concept(code)
            concept = normalize_concept(raw, release_date=release.date, source="live_evs")
            if manifest and concept.release_version != manifest.release_version and not live_only:
                return {
                    "isError": True,
                    "error": "version_mismatch",
                    "message": (
                        "Live EVS concept release does not match the active index. "
                        "Use live_only=true to inspect live data without cache mixing."
                    ),
                    "live_release_version": concept.release_version,
                    "active_index_release_version": manifest.release_version,
                }
            return concept.to_dict(include_raw=include_raw)
        except EVSError as exc:
            if live_only:
                return {"isError": True, "error": "evs_unavailable", "message": str(exc)}
            cached = self.index.get_concept(code, manifest.release_version if manifest else None)
            if cached:
                return cached.to_dict(include_raw=include_raw)
            return {
                "isError": True,
                "error": "evs_unavailable_no_active_cache",
                "message": str(exc),
            }

    def traverse(
        self,
        start_codes: List[str],
        direction: str = "out",
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
        include_hierarchy: bool = True,
        include_roles: bool = True,
        include_associations: bool = True,
        relationship_names: Optional[List[str]] = None,
        edge_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        release = self.evs.resolve_monthly_ncit_release()
        result = traverse_ncit(
            client=self.evs,
            start_codes=start_codes,
            release_version=release.version,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
            include_hierarchy=include_hierarchy,
            include_roles=include_roles,
            include_associations=include_associations,
            relationship_names=relationship_names,
            edge_types=edge_types,
        )
        return result.to_dict()

    def cadsr_status(self) -> Dict[str, Any]:
        return self.cadsr.status().to_dict()
