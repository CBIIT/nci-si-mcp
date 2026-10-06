"""Real MCP and CLI integration of the caDSR registry tools."""

import asyncio
import json
from unittest.mock import patch

from fakes import data_element
from nci_si_mcp.cli import build_parser
from nci_si_mcp.release import RegistryState
from test_cadsr_matching import cde_response, cde_row, vm_response, vm_row
from test_server import ServerFixture


@patch("nci_si_mcp.server.configure_logging")
class CaDSRProtocolTest(ServerFixture):
    def test_matching_is_private_even_alongside_governed_content(self, _):
        async def calls(client):
            return await asyncio.gather(
                client.call_tool("match_data_elements", {"entities": [{"name": "Q"}]}),
                client.call_tool("match_value_meanings", {"values": ["Q"]}),
                client.call_tool("get_data_element", {"publicId": "123"}),
            )

        with (
            patch.object(
                self.context.cadsr,
                "match_data_element",
                return_value=cde_response(matches=[cde_row()])["matchResults"],
            ),
            patch.object(
                self.context.cadsr,
                "match_value_meanings",
                return_value=vm_response(matches=[vm_row()])["matchResults"],
            ),
            patch.object(self.context.cadsr, "get_data_element", return_value=data_element()),
        ):
            cde, vm, content = self.session(calls)
        for result in (cde, vm):
            self.assertFalse(result.is_error)
            self.assertEqual((result.meta["ttlMs"], result.meta["cacheScope"]), (0, "private"))
            self.assertNotIn("_meta", result.structured_content)
            self.assertEqual(len(result.structured_content["matches"]), 1)
        self.assertEqual((content.meta["ttlMs"], content.meta["cacheScope"]), (3_600_000, "public"))

    def test_empty_matching_is_private_and_invalid_nested_input_is_an_error(self, _):
        with patch.object(
            self.context.cadsr, "match_data_element", return_value=cde_response()["matchResults"]
        ):
            empty = self.session(
                lambda client: client.call_tool(
                    "match_data_elements", {"entities": [{"name": "Q"}]}
                )
            )
        self.assertFalse(empty.is_error)
        self.assertEqual(empty.structured_content["matches"], [])
        self.assertEqual((empty.meta["ttlMs"], empty.meta["cacheScope"]), (0, "private"))
        invalid = self.session(
            lambda client: client.call_tool(
                "match_data_elements",
                {
                    "entities": [{"name": "Q"}],
                    "filters": {"classificationScheme": {"publicId": "123"}},
                },
            )
        )
        self.assertTrue(invalid.is_error)
        self.assertEqual(invalid.structured_content["error"]["code"], "invalid_request")
        self.assertEqual(
            invalid.structured_content["error"]["details"]["parameter"],
            "filters.classificationScheme",
        )

    def test_cli_parses_matching_entity_and_scheme_objects(self, _):
        args = build_parser().parse_args(
            [
                "match-data-elements",
                '{"name":"Q"}',
                "--filters",
                '{"classificationScheme":{"publicId":"123","version":"2"}}',
            ]
        )
        self.assertEqual(args.entities, [{"name": "Q"}])
        self.assertEqual(
            args.filters, {"classificationScheme": {"publicId": "123", "version": "2"}}
        )

    def test_concurrent_calls_keep_the_actual_cache_class_in_protocol_metadata(self, _):
        state = RegistryState(None, "2026-07-01T22:19", "releasedCDEsXML-OD.zip")

        async def calls(client):
            return await asyncio.gather(
                client.call_tool("get_data_element", {"publicId": "123"}),
                client.call_tool("resolve_registry_release", {}),
                client.read_resource("cadsr://registry/release"),
                client.read_resource("cadsr://data-element/123/2"),
                client.read_resource("cadsr://data-element/123"),
            )

        with (
            patch.object(self.context.cadsr, "get_data_element", return_value=data_element()),
            patch.object(self.context.cadsr, "resolve_registry_release", return_value=state),
        ):
            item, resolution, resource, versioned, latest = self.session(calls)
        self.assertEqual([item.meta["ttlMs"], resolution.meta["ttlMs"]], [3_600_000, 0])
        self.assertEqual([resource.ttl_ms, versioned.ttl_ms], [3_600_000, 3_600_000])
        self.assertEqual(
            [item.meta["cacheScope"], resolution.meta["cacheScope"], resource.cache_scope],
            ["public"] * 3,
        )
        self.assertNotIn("_meta", item.structured_content)
        self.assertNotIn("ttlMs", item.model_dump(by_alias=True))
        self.assertEqual(
            json.loads(resource.contents[0].text)["provenance"]["source"], "cadsr_export"
        )
        self.assertEqual(json.loads(versioned.contents[0].text)["version"], "2")
        self.assertEqual(json.loads(latest.contents[0].text)["publicId"], "123")
        self.assertEqual(latest.ttl_ms, 3_600_000)

    def test_verified_pinned_tool_content_is_long_lived_and_public(self, _):
        row = {"identifier": "known", "generatedAt": "2026-07-01T22:19"}
        with (
            patch.object(self.context.cadsr, "get_registry_releases", return_value=[row]),
            patch.object(self.context.cadsr, "get_data_element", return_value=data_element()),
        ):
            result = self.session(
                lambda client: client.call_tool(
                    "get_data_element", {"publicId": "123", "registryRelease": "known"}
                )
            )
        self.assertFalse(result.is_error)
        self.assertEqual((result.meta["ttlMs"], result.meta["cacheScope"]), (86_400_000, "public"))
        self.assertEqual(result.structured_content["provenance"]["release"]["identifier"], "known")

    def test_invalid_and_unavailable_calls_are_uncached_private_protocol_errors(self, _):
        for tool, arguments, code in (
            ("get_data_element", {"publicId": 123}, "invalid_request"),
            ("list_classification_schemes", {"context": "TEST"}, "capability_unavailable"),
            ("search_data_elements", {"query": "Q", "filters": {"other": "x"}}, "invalid_request"),
        ):
            with self.subTest(tool=tool):
                result = self.session(
                    lambda client, tool=tool, arguments=arguments: client.call_tool(tool, arguments)
                )
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["error"]["code"], code)
                self.assertEqual((result.meta["ttlMs"], result.meta["cacheScope"]), (0, "private"))

    def test_cli_uses_kebab_flags_and_parses_filter_objects(self, _):
        parser = build_parser()
        args = parser.parse_args(
            ["get-data-element", "--public-id", "123", "--registry-release", "known"]
        )
        self.assertEqual((args.publicId, args.registryRelease), ("123", "known"))
        search = parser.parse_args(["search-data-elements", "Q", "--filters", '{"context":"TEST"}'])
        self.assertEqual(search.filters, {"context": "TEST"})
