"""The cross-domain wire schemas and adapters, exercised through real MCP sessions."""

import asyncio
from unittest.mock import patch

from fakes import FakeSSIS
from nci_si_mcp.cli import build_parser
from test_seam import element, value_row
from test_server import ServerFixture


@patch("nci_si_mcp.server.configure_logging")
class SeamProtocolTest(ServerFixture):
    def test_all_four_tools_serialize_their_real_records_and_cache_policies(self, _):
        self.context.ssis = FakeSSIS(self.settings)
        self.context.ssis.elements = [element(123)]
        self.context.ssis.element_values = [value_row(code="C3262")]
        listing = '<pre><a href="releasedCDEsXML-OD.zip">export</a> 2026-06-05 12:30\n</pre>'

        async def calls(client):
            return await asyncio.gather(
                client.call_tool("find_data_elements_for_concept", {"conceptCode": "C3262"}),
                client.call_tool(
                    "get_concept_for_permissible_value", {"dataElementId": "123", "value": "Male"}
                ),
                client.call_tool(
                    "resolve_stored_value",
                    {"conceptCode": "C3262", "commons": "PDC", "release": "26.06e"},
                ),
                client.call_tool("get_release_alignment", {}),
            )

        with (
            patch.object(self.context.cadsr, "get_crdc_list", return_value=[]),
            patch.object(self.context.cadsr.export_http, "get_text", return_value=listing),
        ):
            uses, concept, stored, alignment = self.session(calls)
        for result in (uses, concept, stored, alignment):
            self.assertFalse(result.is_error, result.content)
        self.assertEqual(
            uses.structured_content["dataElements"][0]["dataElement"]["publicId"], "123"
        )
        self.assertEqual(concept.structured_content["code"], "C3262")
        self.assertEqual(stored.structured_content["confidence"], "none")
        self.assertEqual(alignment.structured_content["intervalDays"], 28)
        self.assertEqual((uses.meta["ttlMs"], concept.meta["cacheScope"]), (0, "private"))
        self.assertEqual((stored.meta["ttlMs"], stored.meta["cacheScope"]), (3_600_000, "public"))
        self.assertEqual((alignment.meta["ttlMs"], alignment.meta["cacheScope"]), (0, "public"))

    def test_cli_preserves_value_text_and_exposes_shared_paging_flags(self, _):
        parser = build_parser()
        value = parser.parse_args(
            ["get-concept-for-permissible-value", "--data-element-id", "123", "--value", " Male "]
        )
        self.assertEqual((value.dataElementId, value.value), ("123", " Male "))
        uses = parser.parse_args(
            [
                "find-data-elements-for-concept",
                "C3262",
                "--include-permissible-values",
                "--limit",
                "2",
            ]
        )
        self.assertEqual(
            (uses.conceptCode, uses.includePermissibleValues, uses.limit), ("C3262", True, 2)
        )
