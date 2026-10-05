import asyncio
import json
import logging
import re
import sys
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from unittest.mock import patch

from mcp.client import Client
from mcp.shared.exceptions import MCPError

from fakes import FakeEVS, concept, release
from nci_si_mcp import handlers
from nci_si_mcp.config import Settings
from nci_si_mcp.context import Context
from nci_si_mcp.embeddings import HashingEmbeddingProvider
from nci_si_mcp.errors import correlated
from nci_si_mcp.http_client import UpstreamUnavailableError
from nci_si_mcp.index import LocalIndex
from nci_si_mcp.registry import invoke
from nci_si_mcp.server import INSTRUCTIONS, create_mcp
from test_docs import QUICKSTART, bullet_names, section

NEOPLASM = concept(
    "C3262",
    "Neoplasm",
    parents=[{"code": "C2991", "name": "Disease or Disorder"}],
    children=[{"code": "C4741", "name": "Neoplasm by Morphology"}],
    roles=[{"type": "Disease_Has_Abnormal_Cell", "relatedCode": "C12922", "relatedName": "Cell"}],
    inverseRoles=[
        {"type": "Gene_Associated_With_Disease", "relatedCode": "C16612", "relatedName": "Gene"}
    ],
    associations=[
        {"type": "Concept_In_Subset", "relatedCode": "C165258", "relatedName": "A Subset"}
    ],
)


MORPHOLOGY = concept("C4741", "Neoplasm by Morphology")


@patch("nci_si_mcp.server.configure_logging")
class ServerStartupTest(unittest.TestCase):
    def test_missing_or_incompatible_mcp_package_is_explained(self, _):
        with (
            patch.dict(sys.modules, {"mcp.server.mcpserver": None}),
            self.assertRaises(RuntimeError) as raised,
        ):
            create_mcp(Settings())

        self.assertIn("'server' extra", str(raised.exception))
        self.assertIn(f"Import failed: {raised.exception.__cause__}", str(raised.exception))


class ServerFixture(unittest.TestCase):
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
        self.evs = FakeEVS([NEOPLASM, MORPHOLOGY])
        self.context = Context(
            self.settings,
            evs=self.evs,
            index=LocalIndex(self.settings.data_dir),
            embedding_provider=HashingEmbeddingProvider(),
        )

    def session(self, interaction):
        """Run `interaction(client)` against the server over an in-process MCP session."""

        async def run():
            async with Client(create_mcp(self.settings, context=self.context)) as client:
                return await interaction(client)

        return asyncio.run(run())

    def call(self, tool, **arguments):
        result = self.session(lambda client: client.call_tool(tool, arguments))
        return result.is_error, json.loads(result.content[0].text)

    def read(self, uri):
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


@patch("nci_si_mcp.server.configure_logging")
class ServerTest(ServerFixture):
    def test_server_reports_its_version_and_its_instructions(self, _):
        async def server_info(client):
            return client.server_info, client.instructions

        info, instructions = self.session(server_info)

        self.assertEqual((info.name, info.version), ("nci-si-mcp", metadata.version("nci-si-mcp")))
        self.assertEqual(instructions, INSTRUCTIONS)

    def test_tools_are_registered_with_descriptions_and_closed_value_sets(self, _):
        tools = {tool.name: tool for tool in self.session(lambda client: client.list_tools()).tools}

        self.assertEqual(len(tools), 11)
        # The closed value sets are advertised in the schemas, wherever the
        # schema generator puts them.
        traverse_schema = json.dumps(tools["ncit_traverse"].input_schema)
        for value in ("both", "inverse_role", "descendant"):
            self.assertIn(f'"{value}"', traverse_schema)
        self.assertIn('"hybrid"', json.dumps(tools["ncit_search"].input_schema))
        for term in ("release_mismatch", "not_found", "fallback", "live_only"):
            self.assertIn(term, tools["ncit_lookup"].description)
        for term in ("truncation", "upstream_cap", "descendant", "relationship_names"):
            self.assertIn(term, tools["ncit_traverse"].description)

    def test_quickstart_lists_the_tools_and_every_argument_of_traverse(self, _):
        tools = {tool.name: tool for tool in self.session(lambda client: client.list_tools()).tools}

        # The names in backticks that start the bullets of the section.
        leads = re.findall(
            r"^- ((?:`\w+`(?:, )?)+)", section(QUICKSTART, "MCP Tools"), flags=re.MULTILINE
        )
        names = {name for lead in leads for name in re.findall(r"`(\w+)`", lead)}

        self.assertEqual(names, set(tools) | set(tools["ncit_traverse"].input_schema["properties"]))

    def test_optional_arguments_have_the_documented_defaults(self, _):
        tools = {tool.name: tool for tool in self.session(lambda client: client.list_tools()).tools}

        def defaults(tool):
            properties = tools[tool].input_schema["properties"]
            return {name: spec["default"] for name, spec in properties.items() if "default" in spec}

        self.assertEqual(defaults("ncit_search"), {"limit": 10, "mode": "hybrid"})
        self.assertEqual(defaults("ncit_lookup"), {"live_only": False})
        self.assertEqual(
            defaults("ncit_traverse"),
            {
                "direction": "out",
                "max_depth": 2,
                "max_nodes": 200,
                "max_edges": 1000,
                "budget_per_kind": None,
                "include_hierarchy": True,
                "include_roles": True,
                "include_associations": True,
                "relationship_names": None,
                "edge_types": None,
            },
        )

    def test_three_resource_templates_are_registered_as_json(self, _):
        templates = self.session(lambda client: client.list_resource_templates()).resource_templates

        self.assertEqual(len(templates), 3)
        self.assertEqual(
            {template.uri_template for template in templates},
            set(bullet_names(section(QUICKSTART, "MCP Resources"))),
        )
        for template in templates:
            self.assertEqual(template.mime_type, "application/json")
            self.assertTrue(template.description)

    def test_tools_return_the_service_results(self, _):
        invoke(self.context, "index_codes", ["C3262"])

        is_error, lookup = self.call("ncit_lookup", code="C3262")
        self.assertFalse(is_error)
        self.assertEqual((lookup["code"], lookup["provenance"]["source"]), ("C3262", "evs_rest"))

        _, search = self.call("ncit_search", query="neoplasm", mode="bm25", limit=1)
        self.assertEqual([hit["concept"]["code"] for hit in search["hits"]], ["C3262"])

        _, traversal = self.call(
            "ncit_traverse", start_codes=["C3262"], max_depth=1, edge_types=["child"]
        )
        self.assertEqual([edge["target_code"] for edge in traversal["edges"]], ["C4741"])

        _, info = self.call("ncit_release_info")
        self.assertEqual(
            info["selected_monthly_release"],
            {
                "terminology": "ncit",
                "channel": "monthly",
                "version": "26.06e",
                "date": "2026-06-29",
            },
        )
        self.assertEqual(info["active_index"]["concept_count"], 1)

        is_error, status = self.call("cadsr_status")
        self.assertFalse(is_error)
        self.assertEqual(status["state"], "reuse_pending")

    def test_release_resources_emit_the_same_public_fields_as_the_tool(self, _):
        _, info = self.call("ncit_release_info")
        expected = info["selected_monthly_release"]

        self.assertEqual(self.read("nci-si://release/ncit/26.06e"), expected)
        for alias in ("monthly", "latest", "monthly-latest"):
            with self.subTest(alias=alias):
                self.assertEqual(
                    self.read(f"nci-si://release/ncit/{alias}")["selected_monthly_release"],
                    expected,
                )

    def test_kind_budget_limits_nodes_through_the_mcp_adapter(self, _):
        self.evs.concepts["C3262"] = dict(self.evs.concepts["C3262"])
        self.evs.concepts["C3262"]["children"] = [
            {"code": "C2", "name": "Two"},
            {"code": "C3", "name": "Three"},
        ]
        is_error, result = self.call(
            "ncit_traverse",
            start_codes=["C3262"],
            max_depth=1,
            edge_types=["child"],
            budget_per_kind=1,
        )
        self.assertFalse(is_error)
        self.assertEqual([node["code"] for node in result["nodes"]], ["C3262", "C2"])
        self.assertEqual(result["truncation"]["bound"], "kind_budget")

    def test_every_tool_argument_shapes_the_result(self, _):
        invoke(self.context, "index_codes", ["C3262", "C4741"])
        arguments = {"query": "neoplasm", "mode": "vector"}
        is_error, search = self.call("ncit_search", **arguments)
        self.assertEqual((is_error, search["mode"], len(search["hits"])), (False, "vector", 2))
        _, search = self.call("ncit_search", limit=1, **arguments)
        self.assertEqual(len(search["hits"]), 1)

        traverse = {
            "start_codes": ["C3262"],
            "direction": "both",
            "max_depth": 1,
            "max_nodes": 40,
            "max_edges": 50,
            "include_hierarchy": False,
            "include_associations": False,
        }
        _, walk = self.call("ncit_traverse", **traverse)
        self.assertEqual((walk["max_depth"], walk["max_nodes"], walk["max_edges"]), (1, 40, 50))
        self.assertEqual({edge["edge_type"] for edge in walk["edges"]}, {"role", "inverse_role"})
        _, walk = self.call("ncit_traverse", **dict(traverse, include_roles=False))
        self.assertEqual(walk["error"]["code"], "invalid_request")
        selection = {"edge_types": ["child", "role"], "relationship_names": ["is_a_child"]}
        _, walk = self.call("ncit_traverse", start_codes=["C3262"], max_depth=1, **selection)
        self.assertEqual([edge["target_code"] for edge in walk["edges"]], ["C4741"])

        # With EVS down, a lookup falls back to the index unless live_only forbids it.
        self.evs.errors = {"get_concept": UpstreamUnavailableError("down")}
        _, cached = self.call("ncit_lookup", code="C3262")
        self.assertEqual(cached["provenance"]["servedBy"], "index")
        is_error, failed = self.call("ncit_lookup", code="C3262", live_only=True)
        self.assertEqual((is_error, failed["error"]["code"]), (True, "upstream_unavailable"))

    def test_error_envelopes_are_flagged_as_protocol_errors(self, _):
        failures = (
            ("not_found", "ncit_lookup", {"code": "C999"}),
            ("invalid_request", "ncit_lookup", {"code": "oops"}),
            ("internal_error", "ncit_search", {"query": "tumor"}),
            ("not_found", "ncit_traverse", {"start_codes": ["C999"]}),
        )
        for code, tool, arguments in failures:
            with self.subTest(tool=tool, code=code):
                is_error, envelope = self.call(tool, **arguments)
                self.assertTrue(is_error)
                self.assertEqual(envelope["error"]["code"], code)
                self.assertTrue(envelope["error"]["message"])

        invoke(self.context, "index_codes", ["C3262"])
        (self.settings.data_dir / "nci_si.sqlite3").write_bytes(b"not a database" * 100)
        is_error, envelope = self.call("ncit_release_info")
        self.assertTrue(is_error)
        self.assertEqual(envelope["error"]["code"], "internal_error")

    def test_an_error_is_the_error_record_as_structured_content_and_as_text(self, _):
        result = self.session(lambda client: client.call_tool("ncit_lookup", {"code": "C999"}))

        self.assertTrue(result.is_error)
        self.assertEqual(set(result.structured_content), {"error"})
        self.assertEqual(result.structured_content, json.loads(result.content[0].text))

    def test_the_error_record_returns_the_correlation_identifier_of_the_call(self, _):
        def failing_lookup(meta):
            return self.session(
                lambda client: client.call_tool("ncit_lookup", {"code": "C999"}, meta=meta)
            ).structured_content["error"]["correlationId"]

        self.assertEqual(failing_lookup({"correlationId": "caller-42"}), "caller-42")
        first, second = failing_lookup(None), failing_lookup({})
        self.assertTrue(first)
        self.assertNotEqual(first, second)

    def test_every_tool_that_can_fail_honours_the_correlation_identifier(self, _):
        calls = {
            "ncit_search": {"query": "tumor"},
            "ncit_traverse": {"start_codes": ["C999"]},
        }
        for tool, arguments in calls.items():
            with self.subTest(tool):
                result = self.session(
                    lambda client, tool=tool, arguments=arguments: client.call_tool(
                        tool, arguments, meta={"correlationId": "c-1"}
                    )
                )

                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["error"]["correlationId"], "c-1")

    def test_every_item_of_a_tool_result_carries_the_correlation_identifier_of_the_call(self, _):
        invoke(self.context, "index_codes", ["C3262"])
        calls = {
            "ncit_lookup": {"code": "C3262"},
            "ncit_search": {"query": "neoplasm"},
            "ncit_traverse": {"start_codes": ["C3262"], "max_depth": 1},
        }

        def provenances(content):
            nodes = content.get("nodes", []) + content.get("edges", [])
            concepts = [hit["concept"] for hit in content.get("hits", [])]
            return [item["provenance"] for item in (nodes or concepts or [content])]

        for tool, arguments in calls.items():
            with self.subTest(tool):
                result = self.session(
                    lambda client, tool=tool, arguments=arguments: client.call_tool(
                        tool, arguments, meta={"correlationId": "c-7"}
                    )
                )

                found = provenances(json.loads(result.content[0].text))
                self.assertTrue(found)
                self.assertEqual({each["correlationId"] for each in found}, {"c-7"})

    def test_no_tool_offers_the_raw_payload_and_no_result_carries_it(self, _):
        invoke(self.context, "index_codes", ["C3262"])
        tools = self.session(lambda client: client.list_tools()).tools

        for tool in tools:
            self.assertNotIn("include_raw", tool.input_schema.get("properties", {}), tool.name)
        _, lookup = self.call("ncit_lookup", code="C3262")
        _, search = self.call("ncit_search", query="neoplasm")
        self.assertNotIn("raw", lookup)
        self.assertNotIn("raw", search["hits"][0]["concept"])

    def test_a_resource_error_carries_a_correlation_identifier_and_details(self, _):
        with self.assertRaises(MCPError) as raised:
            self.read("nci-si://release/ncit/99.99z")

        error = json.loads(str(raised.exception))["error"]
        self.assertTrue(error["correlationId"])
        self.assertEqual(error["details"], {"requested": "99.99z", "source": "evs"})

    def test_an_unavailable_release_resource_names_the_configured_weekly_channel(self, _):
        self.context.settings = replace(self.settings, release_channel="weekly")
        self.evs.release = release("26.07a", "2026-07-06", channel="weekly")

        with self.assertRaises(MCPError) as raised:
            self.read("nci-si://release/ncit/99.99z")

        error = json.loads(str(raised.exception))["error"]
        self.assertEqual(error["code"], "release_not_available")
        self.assertIn("current weekly release", error["message"])
        self.assertIn("26.07a", error["message"])

    def test_the_index_manifest_of_another_release_is_not_available(self, _):
        invoke(self.context, "index_codes", ["C3262"])

        with self.assertRaises(MCPError) as raised:
            self.read("nci-si://index/ncit/99.99z/manifest")

        error = json.loads(str(raised.exception))["error"]
        self.assertEqual(error["code"], "release_not_available")
        self.assertEqual(error["details"], {"requested": "99.99z", "source": "index"})

    def failing_read_ids(self, uri):
        """The correlation identifier of a failing resource read, and those it opened."""

        opened = []

        @contextmanager
        def spy(*arguments):
            with correlated(*arguments) as value:
                opened.append(value)
                yield value

        with patch("nci_si_mcp.invocation.correlated", spy), self.assertRaises(MCPError) as raised:
            self.read(uri)
        return json.loads(str(raised.exception))["error"]["correlationId"], opened

    def test_each_resource_read_runs_under_one_correlation_identifier(self, _):
        invoke(self.context, "index_codes", ["C3262"])
        for uri in (
            "nci-si://concept/ncit/C999",
            "nci-si://release/ncit/99.99z",
            "nci-si://index/ncit/99.99z/manifest",
        ):
            with self.subTest(uri):
                identifier, opened = self.failing_read_ids(uri)

                self.assertEqual(opened, [identifier])

    def test_the_records_of_one_resource_read_share_their_correlation_identifier(self, _):
        self.evs.errors = {
            "get_api_version": UpstreamUnavailableError("down"),
            "get_terminologies": UpstreamUnavailableError("down"),
        }
        reports = []
        release_info = handlers.release_info

        def recording_release_info(context):
            reports.append(release_info(context))
            return reports[-1]

        with patch.object(handlers, "release_info", recording_release_info):
            identifier, _opened = self.failing_read_ids("nci-si://release/ncit/26.06e")

        nested = [reports[0]["evs_api"]["error"], reports[0]["selected_monthly_release"]["error"]]
        self.assertEqual({error["correlationId"] for error in nested}, {identifier})

    def test_a_search_that_finds_nothing_is_a_success_with_no_hits(self, _):
        invoke(self.context, "index_codes", ["C3262"])

        is_error, result = self.call("ncit_search", query="zzzz", mode="bm25")

        self.assertFalse(is_error)
        self.assertEqual(result["hits"], [])

    def test_release_info_stays_a_success_when_evs_is_down(self, _):
        self.evs.errors = {
            "get_api_version": UpstreamUnavailableError("down"),
            "get_terminologies": UpstreamUnavailableError("down"),
        }

        is_error, info = self.call("ncit_release_info")

        self.assertFalse(is_error)
        self.assertEqual(info["selected_monthly_release"]["error"]["code"], "upstream_unavailable")

    def test_resources_route_by_version(self, _):
        self.assertEqual(self.read("nci-si://index/ncit/active/manifest"), {"active_index": None})
        invoke(self.context, "index_codes", ["C3262"])

        self.assertEqual(self.read("nci-si://concept/ncit/C3262")["provenance"]["servedBy"], "live")
        for alias in ("monthly", "latest", "monthly-latest"):
            self.assertIn("active_index", self.read(f"nci-si://release/ncit/{alias}"))
        self.assertEqual(self.read("nci-si://release/ncit/26.06e")["version"], "26.06e")
        for version in ("active", "26.06e"):
            manifest = self.read(f"nci-si://index/ncit/{version}/manifest")
            self.assertEqual(manifest["concept_count"], 1)

    def test_concept_resource_is_a_lookup_with_the_default_options(self, _):
        invoke(self.context, "index_codes", ["C3262"])

        self.assertNotIn("raw", self.read("nci-si://concept/ncit/C3262"))
        self.evs.errors = {"get_concept": UpstreamUnavailableError("down")}
        self.assertEqual(
            self.read("nci-si://concept/ncit/C3262")["provenance"]["servedBy"], "index"
        )

    def test_resource_failures_are_protocol_errors_carrying_the_envelope(self, _):
        invoke(self.context, "index_codes", ["C3262"])
        failures = {
            "nci-si://concept/ncit/C999": "not_found",
            "nci-si://release/ncit/99.99z": "release_not_available",
            "nci-si://index/ncit/99.99z/manifest": "release_not_available",
        }
        for uri, code in failures.items():
            with self.subTest(uri):
                with self.assertRaises(MCPError) as raised:
                    self.read(uri)
                self.assertEqual(json.loads(str(raised.exception))["error"]["code"], code)

        self.evs.errors = {"get_terminologies": UpstreamUnavailableError("down")}
        with self.assertRaises(MCPError) as raised:
            self.read("nci-si://release/ncit/26.06e")
        envelope = json.loads(str(raised.exception))
        self.assertEqual(
            (envelope["error"]["code"], envelope["error"]["message"]),
            ("upstream_unavailable", "down. Retry later."),
        )

        (self.settings.data_dir / "nci_si.sqlite3").write_bytes(b"not a database" * 100)
        for uri in ("nci-si://index/ncit/active/manifest", "nci-si://release/ncit/monthly"):
            with self.subTest(uri):
                with self.assertRaises(MCPError) as raised:
                    self.read(uri)
                self.assertEqual(
                    json.loads(str(raised.exception))["error"]["code"], "internal_error"
                )


if __name__ == "__main__":
    unittest.main()
