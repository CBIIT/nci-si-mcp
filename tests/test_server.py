import asyncio
import json
import logging
import sys
import tempfile
import unittest
from importlib import metadata
from pathlib import Path
from unittest.mock import MagicMock, patch

from fakes import FakeEVS, concept

from nci_si_mcp.config import Settings
from nci_si_mcp.embeddings import HashingEmbeddingProvider
from nci_si_mcp.evs import EVSUnavailableError
from nci_si_mcp.index import LocalIndex
from nci_si_mcp.server import create_mcp
from nci_si_mcp.service import NCISIService

NEOPLASM = concept(
    "C3262",
    "Neoplasm",
    children=[{"code": "C4741", "name": "Neoplasm by Morphology"}],
)


@patch("nci_si_mcp.server.configure_logging")
class ServerStartupTest(unittest.TestCase):
    def test_missing_or_incompatible_mcp_package_is_explained(self, _):
        with patch.dict(sys.modules, {"mcp.server.mcpserver": None}):
            with self.assertRaises(RuntimeError) as raised:
                create_mcp(Settings())

        self.assertIn("'server' extra", str(raised.exception))
        self.assertIn("Import failed", str(raised.exception))


@patch("nci_si_mcp.server.configure_logging")
class ServerTest(unittest.TestCase):
    def setUp(self):
        # Creating an MCPServer installs a root log handler; keep it quiet and
        # take it out again so later tests are not affected.
        root = logging.getLogger()
        self.addCleanup(setattr, root, "handlers", root.handlers[:])
        self.addCleanup(root.setLevel, root.level)
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = Settings(data_dir=Path(directory.name))
        self.evs = FakeEVS([NEOPLASM])
        self.service = NCISIService(
            self.settings,
            evs=self.evs,
            index=LocalIndex(self.settings.data_dir),
            embedding_provider=HashingEmbeddingProvider(),
        )

    def session(self, interaction):
        """Run `interaction(client)` against the server over an in-process MCP session."""

        from mcp.client import Client

        async def run():
            async with Client(create_mcp(self.settings, service=self.service)) as client:
                return await interaction(client)

        return asyncio.run(run())

    def call(self, tool, **arguments):
        result = self.session(lambda client: client.call_tool(tool, arguments))
        return result.is_error, json.loads(result.content[0].text)

    def read(self, uri):
        from mcp.shared.exceptions import MCPError

        async def interaction(client):
            # Caught inside the session: leaving it would wrap the error in a group.
            try:
                return await client.read_resource(uri)
            except MCPError as error:
                return error

        result = self.session(interaction)
        if isinstance(result, MCPError):
            raise result
        self.assertEqual(result.contents[0].mime_type, "application/json")
        return json.loads(result.contents[0].text)

    def test_server_reports_the_installed_package_version(self, _):
        async def server_info(client):
            return client.server_info

        info = self.session(server_info)

        self.assertEqual((info.name, info.version), ("nci-si-mcp", metadata.version("nci-si-mcp")))

    def test_five_tools_are_registered_with_descriptions_and_closed_value_sets(self, _):
        tools = {tool.name: tool for tool in self.session(lambda client: client.list_tools()).tools}

        self.assertEqual(
            set(tools),
            {"ncit_search", "ncit_lookup", "ncit_traverse", "ncit_release_info", "cadsr_status"},
        )
        # The closed value sets are advertised in the schemas, wherever the
        # schema generator puts them.
        traverse_schema = json.dumps(tools["ncit_traverse"].input_schema)
        for value in ("both", "inverse_role", "descendant"):
            self.assertIn(f'"{value}"', traverse_schema)
        self.assertIn('"hybrid"', json.dumps(tools["ncit_search"].input_schema))
        for term in ("version_mismatch", "concept_not_found", "fallback", "live_only"):
            self.assertIn(term, tools["ncit_lookup"].description)
        for term in ("truncated", "unexpanded_codes", "descendant", "relationship_names"):
            self.assertIn(term, tools["ncit_traverse"].description)

    def test_three_resource_templates_are_registered_as_json(self, _):
        templates = self.session(lambda client: client.list_resource_templates()).resource_templates

        self.assertEqual(
            {template.uri_template for template in templates},
            {
                "nci-si://concept/ncit/{code}",
                "nci-si://release/ncit/{version}",
                "nci-si://index/ncit/{version}/manifest",
            },
        )
        for template in templates:
            self.assertEqual(template.mime_type, "application/json")
            self.assertTrue(template.description)

    def test_tools_return_the_service_results(self, _):
        self.service.index_codes(["C3262"])

        is_error, lookup = self.call("ncit_lookup", code="C3262")
        self.assertFalse(is_error)
        self.assertEqual((lookup["code"], lookup["source"]), ("C3262", "live_evs"))

        _, search = self.call("ncit_search", query="neoplasm", mode="bm25", limit=1)
        self.assertEqual([hit["concept"]["code"] for hit in search["hits"]], ["C3262"])

        _, traversal = self.call(
            "ncit_traverse", start_codes=["C3262"], max_depth=1, edge_types=["child"]
        )
        self.assertEqual([edge["target_code"] for edge in traversal["edges"]], ["C4741"])

        _, info = self.call("ncit_release_info")
        self.assertEqual(info["selected_monthly_release"]["version"], "26.06e")
        self.assertEqual(info["active_index"]["concept_count"], 1)

        is_error, status = self.call("cadsr_status")
        self.assertFalse(is_error)
        self.assertEqual(status["state"], "reuse_pending")

    def test_every_tool_argument_reaches_the_service(self, _):
        self.service = MagicMock(spec=NCISIService)
        for method in ("search", "lookup", "traverse"):
            getattr(self.service, method).return_value = {"ok": True}

        search = {"query": "tumor", "limit": 7, "mode": "vector", "include_raw": True}
        self.assertEqual(self.call("ncit_search", **search), (False, {"ok": True}))
        self.service.search.assert_called_once_with(**search)

        lookup = {"code": "C1", "live_only": True, "include_raw": True}
        self.call("ncit_lookup", **lookup)
        self.service.lookup.assert_called_once_with(**lookup)

        traverse = {
            "start_codes": ["C1", "C2"],
            "direction": "both",
            "max_depth": 3,
            "max_nodes": 40,
            "max_edges": 50,
            "include_hierarchy": False,
            "include_roles": False,
            "include_associations": False,
            "relationship_names": ["Has_Finding"],
            "edge_types": ["role", "inverse_role"],
        }
        self.call("ncit_traverse", **traverse)
        self.service.traverse.assert_called_once_with(**traverse)

        self.service.lookup.reset_mock()
        self.read("nci-si://concept/ncit/C1")
        self.service.lookup.assert_called_once_with(code="C1")

    def test_error_envelopes_are_flagged_as_protocol_errors(self, _):
        failures = (
            ("concept_not_found", "ncit_lookup", {"code": "C999"}),
            ("invalid_request", "ncit_lookup", {"code": "oops"}),
            ("no_active_index", "ncit_search", {"query": "tumor"}),
            ("concept_not_found", "ncit_traverse", {"start_codes": ["C999"]}),
        )
        for code, tool, arguments in failures:
            with self.subTest(tool=tool, code=code):
                is_error, envelope = self.call(tool, **arguments)
                self.assertTrue(is_error)
                self.assertTrue(envelope["isError"])
                self.assertEqual(envelope["error"], code)
                self.assertTrue(envelope["message"])

        self.service.index_codes(["C3262"])
        (self.settings.data_dir / "nci_si.sqlite3").write_bytes(b"not a database" * 100)
        is_error, envelope = self.call("ncit_release_info")
        self.assertTrue(is_error)
        self.assertEqual(envelope["error"], "index_storage_error")

    def test_release_info_stays_a_success_when_evs_is_down(self, _):
        self.evs.errors = {
            "get_api_version": EVSUnavailableError("down"),
            "resolve_monthly_ncit_release": EVSUnavailableError("down"),
        }

        is_error, info = self.call("ncit_release_info")

        self.assertFalse(is_error)
        self.assertEqual(info["selected_monthly_release"]["error"], "evs_unavailable")

    def test_resources_route_by_version(self, _):
        self.assertEqual(self.read("nci-si://index/ncit/active/manifest"), {"active_index": None})
        self.service.index_codes(["C3262"])

        self.assertEqual(self.read("nci-si://concept/ncit/C3262")["source"], "live_evs")
        for alias in ("monthly", "latest", "monthly-latest"):
            self.assertIn("active_index", self.read(f"nci-si://release/ncit/{alias}"))
        self.assertEqual(self.read("nci-si://release/ncit/26.06e")["name"], "NCI Thesaurus 26.06e")
        for version in ("active", "26.06e"):
            manifest = self.read(f"nci-si://index/ncit/{version}/manifest")
            self.assertEqual(manifest["concept_count"], 1)

    def test_resource_failures_are_protocol_errors_carrying_the_envelope(self, _):
        from mcp.shared.exceptions import MCPError

        self.service.index_codes(["C3262"])
        failures = {
            "nci-si://concept/ncit/C999": "concept_not_found",
            "nci-si://release/ncit/99.99z": "release_not_active",
            "nci-si://index/ncit/99.99z/manifest": "index_not_active",
        }
        for uri, code in failures.items():
            with self.subTest(uri):
                with self.assertRaises(MCPError) as raised:
                    self.read(uri)
                self.assertEqual(json.loads(str(raised.exception))["error"], code)

        self.evs.errors = {"resolve_monthly_ncit_release": EVSUnavailableError("down")}
        with self.assertRaises(MCPError) as raised:
            self.read("nci-si://release/ncit/26.06e")
        envelope = json.loads(str(raised.exception))
        self.assertEqual((envelope["error"], envelope["message"]), ("evs_unavailable", "down"))

        (self.settings.data_dir / "nci_si.sqlite3").write_bytes(b"not a database" * 100)
        for uri in ("nci-si://index/ncit/active/manifest", "nci-si://release/ncit/monthly"):
            with self.subTest(uri):
                with self.assertRaises(MCPError) as raised:
                    self.read(uri)
                self.assertEqual(json.loads(str(raised.exception))["error"], "index_storage_error")


if __name__ == "__main__":
    unittest.main()
