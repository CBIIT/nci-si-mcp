"""PV primary correction through real MCP; crafted from issue #367's live observation."""

from unittest.mock import patch

from fakes import FakeEVS, FakeSSIS, concept, data_element
from test_server import ServerFixture


def primary_element(value="To a large extent", codes=None, **fields):
    """Contract-crafted metadata, not a recording of CDE 2178439."""
    return data_element(
        "2178439",
        ValueDomain={
            "PermissibleValues": [
                {
                    "publicId": "123",
                    "value": value,
                    "ValueMeaning": {
                        "publicId": "456",
                        "version": "1",
                        "longName": "Crafted value meaning",
                        "Concepts": codes
                        if codes is not None
                        else [
                            {"conceptCode": "C49508", "primaryIndicator": "No", "displayOrder": 1},
                            {"conceptCode": "C25332", "primaryIndicator": "Yes", "displayOrder": 0},
                            {"conceptCode": "C65107", "primaryIndicator": "No", "displayOrder": 2},
                        ],
                    },
                }
            ]
        },
        **fields,
    )


@patch("nci_si_mcp.server.configure_logging")
class PrimaryAPIProtocolTest(ServerFixture):
    def setUp(self):
        super().setUp()
        self.context.evs = FakeEVS([concept("C25332", active=True), concept("C49508", active=True)])
        self.context.ssis = FakeSSIS(self.settings)
        self.context.ssis.element_values = [
            {
                "version": "2",
                "value": "To a large extent",
                "concept": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C49508",
            }
        ]

    def lookup(self, **arguments):
        return self.session(
            lambda client: client.call_tool(
                "get_concept_for_permissible_value",
                {"dataElementId": "2178439", "value": "To a large extent", "release": "26.06e"}
                | arguments,
            )
        )

    def test_primary_flag_wins_over_the_graphs_qualifier(self, _):
        with patch.object(self.context.cadsr, "get_data_element", return_value=primary_element()):
            response = self.lookup()
        self.assertFalse(response.is_error, response.content)
        self.assertEqual(response.structured_content["code"], "C25332")

    def test_provenance_names_only_the_api_and_pinned_evs_reads(self, _):
        with patch.object(self.context.cadsr, "get_data_element", return_value=primary_element()):
            response = self.lookup()
        self.assertFalse(response.is_error, response.content)
        provenance = response.structured_content["provenance"]
        self.assertNotIn("graphs", provenance)
        self.assertEqual(provenance["registry"], {"registry": "cadsr"})
        self.assertEqual(provenance["upstream"]["version"], "26.06e")
        self.assertEqual(
            provenance["upstream"]["cadsr"]["upstream"],
            {"publicId": "2178439", "version": "2", "dateModified": "2026-08-25"},
        )
        self.assertEqual(self.context.ssis.calls, [])

    def test_requested_numeric_version_preserves_api_spelling(self, _):
        with patch.object(
            self.context.cadsr, "get_data_element", return_value=primary_element(version="4.0")
        ) as read:
            response = self.lookup(version="4")
        self.assertFalse(response.is_error, response.content)
        self.assertEqual(
            response.structured_content["permissibleValue"]["dataElement"],
            {"publicId": "2178439", "version": "4.0"},
        )
        self.assertEqual(read.call_args.args, ("2178439", "4"))
