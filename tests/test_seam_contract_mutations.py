"""Cross-domain privacy and rich wire records from the independent mutation review."""

from copy import deepcopy
from unittest.mock import patch

from jsonschema import Draft202012Validator

from fakes import FakeSSIS
from nci_si_mcp.cli import build_parser
from nci_si_mcp.evs import EVSClient
from nci_si_mcp.registry import invoke
from test_audit import captured, records
from test_audit_mutations import digest
from test_seam import NCIT, element, gdc_map, value_row
from test_server import ServerFixture


@patch("nci_si_mcp.server.configure_logging")
class SeamContractMutationTest(ServerFixture):
    def setUp(self):
        super().setUp()
        self.context.ssis = FakeSSIS(self.settings)

    def validated(self, name, arguments):
        async def interaction(client):
            return await client.list_tools(), await client.call_tool(name, arguments)

        listing, response = self.session(interaction)
        self.assertFalse(response.is_error, response.content)
        tool = next(tool for tool in listing.tools if tool.name == name)
        validator = Draft202012Validator(tool.output_schema)
        data = response.structured_content
        validator.validate(data)
        return validator, data

    def rejects_missing(self, validator, data, paths):
        for path in paths:
            with self.subTest(path=path):
                broken = deepcopy(data)
                parent = broken
                for key in path[:-1]:
                    parent = parent[key]
                del parent[path[-1]]
                self.assertFalse(validator.is_valid(broken))

    def rejects_types(self, validator, data, cases):
        for path, value in cases:
            with self.subTest(path=path, value=value):
                broken = deepcopy(data)
                parent = broken
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = value
                self.assertFalse(validator.is_valid(broken))

    def test_value_uses_and_required_provenance_survive_the_public_schema(self, _):
        self.context.ssis.elements = [element(123)]
        self.context.ssis.values = [
            {"id": "123", "version": "1", "value": "Male", "concept": NCIT + "C3262"}
        ]
        validator, data = self.validated(
            "find_data_elements_for_concept",
            {"conceptCode": "C3262", "includePermissibleValues": True},
        )
        self.assertEqual(data["permissibleValues"][0]["value"], "Male")
        self.assertEqual(data["permissibleValues"][0]["conceptCode"], "C3262")
        self.assertEqual(data["permissibleValues"][0]["conceptTerminology"], "ncit")
        self.rejects_missing(
            validator,
            data,
            [("truncation",), ("dataElements", 0, "provenance"), ("permissibleValues", 0, "value")],
        )
        self.rejects_types(
            validator, data, [(("permissibleValues", 0, "dataElement", "publicId"), 123)]
        )

    def test_permissible_value_identity_is_required_in_the_concept_output(self, _):
        self.context.ssis.element_values = [value_row(code="C3262")]
        validator, data = self.validated(
            "get_concept_for_permissible_value", {"dataElementId": "123", "value": "Male"}
        )
        self.assertEqual(data["permissibleValue"]["value"], "Male")
        self.assertEqual(data["provenance"]["servedBy"], "live")
        self.assertEqual(
            data["provenance"]["sourceUri"],
            self.evs.uri("/api/v1/concept/ncit_26.06e/C3262"),
        )
        self.rejects_missing(validator, data, [("permissibleValue",)])

    def test_gdc_hit_and_evidence_have_strict_mapset_source_records(self, _):
        self.context.evs = EVSClient("https://evs.test")
        with patch.object(
            self.context.evs,
            "_get_existing",
            side_effect=[
                self.evs.get_terminologies(),
                {"code": "NCIt_Maps_To_GDC", "version": "26.06e"},
                {"total": 1, "maps": [gdc_map("C3262")]},
            ],
        ):
            validator, data = self.validated(
                "resolve_stored_value",
                {"conceptCode": "C3262", "commons": "GDC", "release": "26.06e"},
            )
        self.assertEqual(data["confidence"], "asserted")
        self.assertEqual(data["storedValues"][0]["source"]["version"], "26.06e")
        self.assertEqual(data["evidence"]["sources"], [data["storedValues"][0]["source"]])
        self.rejects_missing(
            validator,
            data,
            [("storedValues", 0, "source"), ("storedValues", 0, "source", "version")],
        )
        self.rejects_types(
            validator,
            data,
            [
                (("confidence",), "probable"),
                (("storedValues", 0, "source", "version"), 26),
                (("evidence", "sources"), ["mapset"]),
                (("evidence", "valueLevelBinding"), "yes"),
                (("evidence", "coverage"), "1"),
            ],
        )

    def test_crdc_hit_and_evidence_have_strict_crosswalk_source_records(self, _):
        row = {
            "CDE Public ID": "123",
            "Version": "2",
            "CRDC Name": "diagnosis",
            "Used By": "PDC",
            "permissibleValues": [{"Permissible Value": "Tumor", "Concept Code": "C3262"}],
        }
        with patch.object(self.context.cadsr, "get_crdc_list", return_value=[row]):
            validator, data = self.validated(
                "resolve_stored_value", {"conceptCode": "C3262", "commons": "PDC"}
            )
        self.assertEqual(data["confidence"], "asserted")
        self.assertEqual(data["storedValues"][0]["source"]["crosswalk"], "CRDC")
        self.assertEqual(data["evidence"]["sources"], [data["storedValues"][0]["source"]])
        self.rejects_missing(validator, data, [("storedValues", 0, "source", "crosswalk")])
        self.rejects_types(validator, data, [(("storedValues", 0, "source", "crosswalk"), 7)])

    def test_cross_domain_terminology_input_is_advertised_as_ncit_only(self, _):
        listing = self.session(lambda client: client.list_tools())
        tool = next(tool for tool in listing.tools if tool.name == "find_data_elements_for_concept")
        declaration = tool.input_schema["properties"]["terminology"]
        self.assertEqual(declaration.get("enum", [declaration.get("const")]), ["ncit"])

    def test_all_cross_domain_parameters_keep_their_audit_privacy_class(self, _):
        calls = (
            (
                "find_data_elements_for_concept",
                {
                    "conceptCode": "bad",
                    "terminology": "ncit",
                    "release": "26.06e",
                    "expandDescendants": True,
                    "includePermissibleValues": True,
                    "limit": 17,
                    "cursor": "cursor-canary",
                },
                {"cursor"},
            ),
            (
                "get_concept_for_permissible_value",
                {
                    "permissibleValueId": "456",
                    "dataElementId": "123",
                    "value": "value-canary",
                    "release": "26.06e",
                },
                {"value"},
            ),
            (
                "resolve_stored_value",
                {
                    "conceptCode": "bad",
                    "commons": "commons-canary",
                    "dataElementId": "123",
                    "release": "26.06e",
                },
                {"commons"},
            ),
            ("get_release_alignment", {"maxIntervalDays": -1}, set()),
        )
        for name, arguments, private in calls:
            with self.subTest(tool=name), captured() as stream:
                result = invoke(self.context, name, **arguments)
                self.assertEqual(result["error"]["code"], "invalid_request")
                expected = {
                    key: {"sha256": digest(value)} if key in private else value
                    for key, value in arguments.items()
                }
                self.assertEqual(records(stream)[0]["parameters"], expected)
                self.assertTrue(all(arguments[key] not in stream.getvalue() for key in private))

    def test_stored_value_and_alignment_cli_commands_keep_their_public_flags(self, _):
        parser = build_parser()
        stored = parser.parse_args(["resolve-stored-value", "C3262", "GDC", "--release", "26.06e"])
        self.assertEqual(
            (stored.conceptCode, stored.commons, stored.release), ("C3262", "GDC", "26.06e")
        )
        alignment = parser.parse_args(["get-release-alignment", "--max-interval-days", "0"])
        self.assertEqual(alignment.maxIntervalDays, 0)
