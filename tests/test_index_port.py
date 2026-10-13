"""The server reads a port; the portable package can run without the server."""

import json
import subprocess
import sys
import unittest
from dataclasses import replace

from fakes import FakeIndex
from nci_si_mcp.evs import normalize_concept
from nci_si_mcp.http_client import UpstreamUnavailableError
from nci_si_mcp.models import IndexManifest, SearchHit
from nci_si_mcp.registry import invoke
from test_server import NEOPLASM, ServerFixture


class ReadPortTest(ServerFixture):
    def test_registry_reads_in_memory_snapshots_without_operator_methods(self):
        manifest = IndexManifest(
            "ncit",
            "26.06e",
            "2026-06-29",
            "hashing",
            "hashing-128",
            1,
            "2026-06-29T00:00:00Z",
            "memory",
            build_id="a" * 32,
        )
        concept = normalize_concept(NEOPLASM, "2026-06-29", "active_cache", manifest.built_at)
        self.context.index = FakeIndex(manifest, [SearchHit(concept, 0.75, 1, "name")])
        result = invoke(
            self.context, "search_concepts", terminology="ncit", query="tumor", mode="semantic"
        )
        self.assertEqual(result["totalKnown"], 1)
        self.assertEqual(result["results"][0]["score"], 0.75)
        self.assertEqual(result["results"][0]["concept"]["code"], "C3262")
        self.assertEqual(
            result["results"][0]["concept"]["provenance"]["retrievedAt"], "2026-06-29T00:00:00Z"
        )
        resource = invoke(self.context, "index_resource", release="26.06e")
        self.assertEqual(resource["builtAt"], "2026-06-29T00:00:00Z")
        self.evs.errors["get_concept"] = UpstreamUnavailableError("offline")
        cached = invoke(self.context, "lookup", code="C3262")
        self.assertEqual(cached["preferred_name"], "Neoplasm")
        self.assertEqual(cached["provenance"]["source"], "evs_index")
        self.context.index = FakeIndex(replace(manifest, concept_count=0), [])
        empty = invoke(self.context, "search", query="missing")
        self.assertEqual(empty["hits"], [])
        self.assertEqual(empty["provenance"]["release"]["identifier"], "26.06e")

    def test_cli_search_reports_both_sides_of_the_page_bound(self):
        invoke(self.context, "index_codes", ["C3262", "C4741"])
        limited = invoke(self.context, "search", query="tumor", limit=1)
        self.assertEqual(
            limited["truncation"],
            {
                "occurred": True,
                "bound": "results",
                "limit": 1,
                "reached": 1,
                "omitted": 1,
                "exact": True,
            },
        )
        complete = invoke(self.context, "search", query="tumor", limit=2)
        self.assertEqual(complete["truncation"], {"occurred": False})
        self.assertEqual(len(complete["hits"]), 2)


class PortablePackageTest(unittest.TestCase):
    def test_bm25_operates_with_server_and_optional_imports_blocked(self):
        script = """
import importlib, importlib.abc, json, pkgutil, sys, tempfile
from pathlib import Path
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'nci_si_mcp', 'mcp', 'numpy', 'sentence_transformers'}:
            raise RuntimeError('forbidden import: ' + fullname)
sys.meta_path.insert(0, BlockImports())
import ncit_index
for module in pkgutil.walk_packages(ncit_index.__path__, ncit_index.__name__ + '.'):
    importlib.import_module(module.name)
from ncit_index.index import LocalIndex
from ncit_index.records import Concept
from ncit_index.embeddings import HashingEmbeddingProvider
with tempfile.TemporaryDirectory() as directory:
    index = LocalIndex(Path(directory))
    concept = Concept('C1', 'Neoplasm', 'NCI Thesaurus', 'ncit', '26.06e', None,
                      '2026-06-29T00:00:00Z', 'active_cache')
    provider = HashingEmbeddingProvider()
    index.upsert_concepts([concept], None, provider)
    hits, total, manifest = index.search_page('Neoplasm', provider, mode='bm25')
    print(json.dumps([[(hit.concept.code, hit.score, hit.rank, hit.matched_on)
                      for hit in hits], total, manifest.release_version]))
"""
        result = subprocess.run(  # noqa: S603 -- fixed script in the project interpreter
            [sys.executable, "-c", script], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [[["C1", 1.0, 1, "name"]], 1, "26.06e"])
