"""Given recorded graph assertions, MCP names their roles without inferring primary."""

import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

from nci_si_mcp.config import Settings
from nci_si_mcp.ssis import SSISClient
from test_http_client import Reply, ServerTestCase
from test_server import ServerFixture
from test_ssis_client import recording

FIXTURES = Path(__file__).resolve().parents[1] / "acceptance/fixtures/recorded/ssis-sparql"


class GraphRoleProtocolTest(ServerFixture):
    def test_graph_status_sets_preserve_unpaired_public_id_evidence(self):
        """Given merged DE2672191, finding it retains both independent status sets."""
        source = recording("ssis-sparql/data-elements-statuses-c48179.json")
        identities = recording("ssis-sparql/graph-identities.json")["response"]["body"]
        self.context.ssis = SSISClient(self.settings)

        async def call(client):
            return await client.call_tool(
                "find_data_elements_for_concept", {"conceptCode": "C48179", "limit": 1000}
            )

        with patch.object(
            self.context.ssis.sparql_http,
            "post_form",
            side_effect=[identities, source["response"]["body"]],
        ):
            result = self.session(call)
        self.assertFalse(result.is_error, result.content)
        use = next(
            row
            for row in result.structured_content["dataElements"]
            if row["dataElement"]["publicId"] == "2672191"
        )
        self.assertEqual(use.get("workflowStatuses"), ["RELEASED", "RETIRED ARCHIVED"])
        self.assertEqual(use.get("registrationStatuses"), ["Standard", "Superceded"])

    def test_recorded_value_order_survives_mcp_without_primary_inference(self):
        """When finding C49508, its graph-main use in 2178439 retains order one."""
        source = recording("ssis-sparql/data-elements-roles-c49508.json")
        identities = recording("ssis-sparql/graph-identities.json")["response"]["body"]
        self.context.ssis = SSISClient(self.settings)

        async def call(client):
            return await client.call_tool(
                "find_data_elements_for_concept", {"conceptCode": "C49508", "limit": 1000}
            )

        with patch.object(
            self.context.ssis.sparql_http,
            "post_form",
            side_effect=[identities, source["response"]["body"]],
        ):
            result = self.session(call)
        self.assertFalse(result.is_error, result.content)
        use = next(
            row
            for row in result.structured_content["dataElements"]
            if row["dataElement"]["publicId"] == "2178439"
        )
        self.assertEqual(
            use.get("roles"),
            [{"role": "valueMeaning", "conceptCode": "C49508", "displayOrder": 1}],
        )


class GraphRoleWireTest(ServerTestCase):
    def test_final_probed_query_is_the_exact_wire_contract(self):
        """Complete tuples aggregate before the public-ID bound, in both query modes."""
        for suffix, expand in (("", False), ("-descendants", True)):
            with self.subTest(expand=expand):
                source = json.loads(
                    (FIXTURES / f"data-elements-roles-c25332{suffix}.json").read_text()
                )
                server = self.serve(Reply(body=b'{"results":{"bindings":[]}}'))
                client = SSISClient(Settings(ssis_sparql_url=server.url))
                self.assertEqual(client.find_data_elements("C25332", expand_descendants=expand), [])
                self.assertEqual(
                    parse_qs(server.bodies[0].decode())["query"][0],
                    source["request"]["form"]["query"],
                )
