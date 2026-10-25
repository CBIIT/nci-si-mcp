"""PV primary correction through real MCP; crafted from issue #367's live observation."""

import json
from pathlib import Path
from unittest.mock import patch

from fakes import FakeEVS, FakeSSIS, concept, data_element
from nci_si_mcp.evs import EVSClient
from nci_si_mcp.http_client import UpstreamUnavailableError
from nci_si_mcp.registry import invoke
from test_evs_client import FakeResponse
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

    def test_invalid_version_refuses_before_any_upstream_request(self, _):
        terminologies = self.context.evs.get_terminologies()
        self.context.evs = EVSClient(self.settings.evs_base_url)
        with patch(
            "nci_si_mcp.http_client._open",
            return_value=FakeResponse(json.dumps(terminologies).encode()),
        ) as requests:
            # The registry exercises the tool's validation without MCP's schema guard.
            result = invoke(
                self.context,
                "get_concept_for_permissible_value",
                dataElementId="2178439",
                value="To a large extent",
                version="4.x",
            )
        self.assertEqual(set(result), {"error"})
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(
            result["error"]["details"],
            {"parameter": "version", "reason": "version must match ^[0-9]+([.][0-9]+)?$"},
        )
        self.assertEqual(requests.call_args_list, [])
        self.assertEqual(self.context.ssis.calls, [])

    def test_primary_flag_wins_over_the_graphs_qualifier(self, _):
        # Keep the observed inversion, and separately challenge display-order selection.
        for codes in (
            None,
            [
                {"conceptCode": "C49508", "primaryIndicator": "No", "displayOrder": 0},
                {"conceptCode": "C25332", "primaryIndicator": "Yes", "displayOrder": 2},
            ],
        ):
            with (
                self.subTest(codes=codes),
                patch.object(
                    self.context.cadsr,
                    "get_data_element",
                    return_value=primary_element(codes=codes),
                ),
            ):
                response = self.lookup()
            self.assertFalse(response.is_error, response.content)
            self.assertEqual(response.structured_content["code"], "C25332")

    def test_provenance_names_only_the_api_and_pinned_evs_reads(self, _):
        with patch.object(
            self.context.cadsr, "get_data_element", return_value=primary_element(version="4")
        ):
            response = self.lookup(version="4.00")
        self.assertFalse(response.is_error, response.content)
        provenance = response.structured_content["provenance"]
        self.assertNotIn("graphs", provenance)
        self.assertEqual(provenance["registry"], {"registry": "cadsr"})
        self.assertEqual(
            provenance["release"],
            {"terminology": "ncit", "identifier": "26.06e", "date": "2026-06-29"},
        )
        self.assertEqual(provenance["source"], "evs_rest")
        self.assertEqual(
            provenance["sourceUri"], self.context.evs.uri("/api/v1/concept/ncit_26.06e/C25332")
        )
        self.assertEqual(provenance["upstream"]["version"], "26.06e")
        origin = provenance["upstream"]["cadsr"]
        self.assertEqual(origin["release"], {"registry": "cadsr"})
        self.assertEqual(origin["source"], "cadsr_rest")
        self.assertEqual(
            origin["sourceUri"],
            self.settings.cadsr_base_url + "/NCIAPI/1.0/api/DataElement/2178439?version=4.00",
        )
        self.assertEqual(
            provenance["upstream"]["cadsr"]["upstream"],
            {"publicId": "2178439", "version": "4", "dateModified": "2026-08-25"},
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

        # Given a recorded value exclusive to v1, requesting it succeeds only at v1.
        # EVS is an explicit unit double, not an invented upstream recording.
        self.context.evs = FakeEVS([concept("C38046", active=True)])
        fixtures = Path(__file__).parents[1] / "acceptance/fixtures/recorded/cadsr"
        for version, filename in (
            ("1", "data-element-2200604-version-1.json"),
            (None, "data-element-2200604.json"),
        ):
            body = json.loads((fixtures / filename).read_text())["response"]["body"]
            with (
                self.subTest(version=version),
                patch(
                    "nci_si_mcp.http_client._open",
                    return_value=FakeResponse(json.dumps(body).encode()),
                ),
            ):
                response = self.lookup(
                    dataElementId="2200604", value="Not Specified", version=version
                )
            if version is not None:
                self.assertFalse(response.is_error, response.content)
                self.assertEqual(response.structured_content["code"], "C38046")
                self.assertEqual(
                    response.structured_content["permissibleValue"],
                    {
                        "dataElement": {"publicId": "2200604", "version": "1"},
                        "value": "Not Specified",
                    },
                )
            else:
                self.assertTrue(response.is_error)
                self.assertEqual(response.structured_content["error"]["code"], "not_found")

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
