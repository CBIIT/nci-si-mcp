"""PV primary correction through real MCP; crafted from issue #367's live observation."""

from unittest.mock import patch

from fakes import FakeEVS, FakeSSIS, concept, data_element
from nci_si_mcp.http_client import UpstreamUnavailableError
from test_server import ServerFixture


def primary_element(value="To a large extent", codes=None, **fields):
    """Contract-crafted metadata, not a recording of CDE 2178439."""
    return data_element(
        fields.pop("publicId", "2178439"),
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
        self.context.ssis = FakeSSIS(self.settings)
        self.context.ssis.element_values = [
            {
                "version": "2",
                "value": "To a large extent",
                "concept": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C49508",
            }
        ]
        qualifier = self.context.ssis.element_values[0]["concept"].rpartition("#")[2]
        self.context.evs = FakeEVS(
            [concept("C25332", active=True), concept(qualifier, active=True)]
        )

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

    def test_primary_evidence_never_comes_from_order_or_an_invalid_flag(self, _):
        cases = [
            [],
            [{"conceptCode": "C25332", "primaryIndicator": "No", "displayOrder": 0}],
            [{"conceptCode": "C25332"}],
            [{"conceptCode": "C25332", "primaryIndicator": "yes"}],
            [{"conceptCode": "C25332", "primaryIndicator": True}],
            [
                {"conceptCode": "C25332", "primaryIndicator": "Yes"},
                {"conceptCode": "C49508", "primaryIndicator": "Yes"},
            ],
        ]
        for codes in cases:
            with (
                self.subTest(codes=codes),
                patch.object(
                    self.context.cadsr,
                    "get_data_element",
                    return_value=primary_element(codes=codes),
                ),
            ):
                response = self.lookup()
                self.assertTrue(response.is_error)
                error = response.structured_content["error"]
                self.assertEqual(error["code"], "upstream_unavailable")
                self.assertEqual(error["details"]["surface"], "cadsr")
                self.assertNotIn("code", response.structured_content)

    def test_identical_primary_assertions_collapse_without_using_array_position(self, _):
        codes = [{"conceptCode": "C25332", "primaryIndicator": "Yes"}] * 2
        with patch.object(
            self.context.cadsr, "get_data_element", return_value=primary_element(codes=codes)
        ):
            response = self.lookup()
        self.assertFalse(response.is_error, response.content)
        self.assertEqual(response.structured_content["code"], "C25332")

    def test_each_exact_value_row_must_agree_on_one_primary(self, _):
        raw = primary_element()
        values = raw["ValueDomain"]["PermissibleValues"]
        for codes in (
            [],
            [{"conceptCode": "C49508", "primaryIndicator": "Yes"}],
        ):
            other = primary_element(codes=codes)["ValueDomain"]["PermissibleValues"][0]
            with (
                self.subTest(codes=codes),
                patch.object(
                    self.context.cadsr,
                    "get_data_element",
                    return_value=raw | {"ValueDomain": {"PermissibleValues": [*values, other]}},
                ),
            ):
                response = self.lookup()
            self.assertTrue(response.is_error)
            self.assertEqual(response.structured_content["error"]["code"], "upstream_unavailable")

    def test_api_failures_never_fall_back_to_the_graph(self, _):
        with patch.object(
            self.context.cadsr,
            "get_data_element",
            side_effect=UpstreamUnavailableError("caDSR unavailable"),
        ):
            response = self.lookup()
        self.assertTrue(response.is_error)
        self.assertEqual(response.structured_content["error"]["code"], "upstream_unavailable")
        self.assertEqual(self.context.ssis.calls, [])

    def test_missing_exact_values_do_not_use_graph_or_an_older_version(self, _):
        raw = primary_element()
        for value in (
            "to a large extent",
            " To a large extent",
            "To a large extent ",
            "older value",
        ):
            with (
                self.subTest(value=value),
                patch.object(self.context.cadsr, "get_data_element", return_value=raw),
            ):
                response = self.lookup(value=value)
            self.assertTrue(response.is_error)
            self.assertEqual(response.structured_content["error"]["code"], "not_found")
        self.assertEqual(self.context.ssis.calls, [])
