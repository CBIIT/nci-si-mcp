"""Matching boundary regressions exercised through the public invocation path."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from nci_si_mcp.cadsr import CDE_MATCH, VM_MATCH
from nci_si_mcp.config import Settings
from nci_si_mcp.context import Context
from nci_si_mcp.registry import invoke
from test_cadsr_client import reply
from test_cadsr_matching import cde_response, cde_row, vm_response, vm_row
from test_http_client import ServerTestCase


class MatchingBoundariesTest(ServerTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def call(self, server, operation="match_data_elements", **args):
        context = Context(Settings(data_dir=self.directory, cadsr_base_url=server.url))
        return invoke(context, operation, **args)

    def assert_invalid(self, operation, defaults, cases):
        server = self.serve()
        for overrides, parameter in cases:
            with self.subTest(overrides=overrides):
                result = self.call(
                    server, operation, **(defaults | overrides | {"registryRelease": "unlisted"})
                )
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(result["error"]["details"]["parameter"], parameter)
                self.assertEqual(server.seen, [])

    def test_invalid_entities_are_rejected_before_pinned_registry_discovery(self):
        cases = [
            ({"entities": None}, "entities"),
            ({"entities": "Q"}, "entities"),
            ({"entities": [[]]}, "entities"),
            ({"entities": [{}]}, "entities.name"),
            ({"entities": [{"name": " \t\n"}]}, "entities.name"),
            ({"entities": [{"name": 5}]}, "entities.name"),
            ({"matchLimit": 0}, "matchLimit"),
            ({"modelVariant": "model"}, "modelVariant"),
            ({"similarityThreshold": 0.5}, "similarityThreshold"),
        ]
        self.assert_invalid("match_data_elements", {"entities": [{"name": "Q"}]}, cases)

    def test_invalid_descriptions_are_rejected_before_pinned_registry_discovery(self):
        cases = []
        for value in ("", " \t\n", None, 5):
            cases.append(({"entities": [{"name": "Q", "userTip": value}]}, "entities.userTip"))
            cases.append(
                (
                    {"entities": [{"name": "Q", "permissibleValues": [value]}]},
                    "entities.permissibleValues",
                )
            )
        self.assert_invalid("match_data_elements", {"entities": [{"name": "Q"}]}, cases)

    def test_invalid_filter_headers_are_rejected_before_pinned_registry_discovery(self):
        cases = [
            ({"filters": {key: value}}, f"filters.{key}")
            for key in ("context", "workflowStatus", "registrationStatus", "valueDomainType")
            for value in ("", " \t", None, 5)
        ]
        self.assert_invalid("match_data_elements", {"entities": [{"name": "Q"}]}, cases)

    def test_invalid_scheme_identity_is_rejected_before_pinned_registry_discovery(self):
        schemes = [None, [], {"publicId": "123", "version": "1", "extra": "x"}]
        schemes.extend({"publicId": value, "version": "1"} for value in (0, 123, "0", "007", ""))
        schemes.extend({"publicId": "123", "version": value} for value in (1, "", "1.", "1.2.3"))
        self.assert_invalid(
            "match_data_elements",
            {"entities": [{"name": "Q"}]},
            [
                ({"filters": {"classificationScheme": value}}, "filters.classificationScheme")
                for value in schemes
            ],
        )

    def test_invalid_values_and_headers_are_rejected_before_pinned_registry_discovery(self):
        cases = [({"values": value}, "values") for value in (None, "Q", [5], [" \t\n"])]
        cases.extend(
            ({"terminologyScope": [value]}, "terminologyScope")
            for value in ("é", "NCI\n", "NCI\x00", 5)
        )
        cases.append(({"strictness": "other"}, "strictness"))
        self.assert_invalid("match_value_meanings", {"values": ["Q"]}, cases)

    def test_ten_entities_are_accepted_with_default_match_limit(self):
        names = [f"Q{number}" for number in range(10)]
        server = self.serve(*(reply(cde_response(name, [cde_row()])) for name in names))
        result = self.call(
            server, entities=[{"name": name, "permissibleValues": []} for name in names]
        )
        self.assertIn("matches", result)
        self.assertEqual([match["entity"] for match in result["matches"]], names)
        self.assertEqual([h["matchlimit"] for _, h in server.seen], ["10"] * 10)

    def test_explicit_empty_permissible_values_are_preserved_in_request(self):
        server = self.serve(reply(cde_response(matches=[cde_row()])))
        result = self.call(server, entities=[{"name": "Q", "permissibleValues": []}])
        self.assertIn("matches", result)
        self.assertEqual(
            json.loads(server.bodies[0]),
            {"entity": "Q", "pvvmData": []},
        )

    def test_ten_values_preserve_request_order_and_duplicate_occurrences(self):
        names = ["Z", "A", "Z", "B", "C", "D", "E", "F", "G", "H"]
        groups = [
            {"name": name, "matches": [vm_row(itemId=str(100 + i))]} for i, name in enumerate(names)
        ]
        server = self.serve(reply({"matchResults": groups}))
        result = self.call(server, "match_value_meanings", values=names)
        self.assertIn("matches", result)
        self.assertEqual(json.loads(server.bodies[0]), [{"name": name} for name in names])
        self.assertEqual(
            [m["item"]["publicId"] for m in result["matches"]], [str(100 + i) for i in range(10)]
        )

    def test_nonempty_matches_have_only_item_provenance_with_the_correct_endpoint(self):
        cases = (
            (
                "match_data_elements",
                {"entities": [{"name": "Q"}]},
                cde_response(matches=[cde_row()]),
                "dataElement",
                CDE_MATCH,
            ),
            (
                "match_value_meanings",
                {"values": ["Q"]},
                vm_response(matches=[vm_row()]),
                "item",
                VM_MATCH,
            ),
        )
        for operation, args, response, key, endpoint in cases:
            with self.subTest(operation=operation):
                server = self.serve(reply(response))
                result = self.call(server, operation, **args)
                self.assertNotIn("provenance", result)
                provenance = result["matches"][0][key]["provenance"]
                self.assertEqual(provenance["source"], "cadsr_rest")
                self.assertEqual(provenance["sourceUri"], server.url + endpoint)

    def assert_malformed(self, operation, args, responses):
        for response in responses:
            with self.subTest(response=response):
                result = self.call(self.serve(reply(response)), operation, **args)
                self.assertIn("error", result)
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.assertNotIn("matches", result)

    def test_cde_required_match_text_and_score_are_validated(self):
        rows = [
            cde_row(**{key: value})
            for key in ("matchedText", "ruleDescription")
            for value in (None, 5)
        ]
        rows.extend(cde_row(score=value) for value in (None, "0.5"))
        absent = cde_row()
        del absent["score"]
        rows.append(absent)
        self.assert_malformed(
            "match_data_elements",
            {"entities": [{"name": "Q"}]},
            [cde_response(matches=[row]) for row in rows],
        )

    def test_vm_item_identity_and_closed_item_type_are_validated(self):
        rows = [vm_row(itemId=value) for value in (None, 123, "0", "007", "x")]
        rows.extend(vm_row(version=value) for value in (None, 1, "", "1.", "1.2.3"))
        rows.extend(vm_row(itemType=value) for value in ("concept", "DataElement", None))
        self.assert_malformed(
            "match_value_meanings", {"values": ["Q"]}, [vm_response(matches=[row]) for row in rows]
        )

    def test_vm_optional_text_and_required_rule_are_validated(self):
        rows = [
            vm_row(**{key: 5})
            for key in ("concept", "evsSource", "context", "workflowStatus", "registrationStatus")
        ]
        missing = vm_row()
        del missing["ruleDescription"]
        rows.append(missing)
        rows.append(vm_row(ruleDescription=5))
        rows.append(vm_row(crosswalkCode="mapped", crosswalkDescription=None))
        self.assert_malformed(
            "match_value_meanings", {"values": ["Q"]}, [vm_response(matches=[row]) for row in rows]
        )

    def test_vm_context_and_workflow_status_are_required_never_null(self):
        rows = []
        for key in ("context", "workflowStatus"):
            missing = vm_row()
            del missing[key]
            rows.extend((missing, vm_row(**{key: None})))
        self.assert_malformed(
            "match_value_meanings",
            {"values": ["Q"]},
            [vm_response(matches=[row]) for row in rows],
        )

    def test_vm_present_optional_fields_survive_and_null_scores_and_crosswalks_are_absent(self):
        absent = vm_row(score=None, registrationStatus="Qualified")
        del absent["crosswalkCode"]
        rows = [absent, vm_row(score=None, crosswalkCode=None, crosswalkDescription=None)]
        result = self.call(
            self.serve(reply(vm_response(matches=rows))), "match_value_meanings", values=["Q"]
        )
        self.assertIn("matches", result)
        first = result["matches"][0]["item"]
        self.assertTrue({"context", "workflowStatus", "registrationStatus"}.issubset(first))
        self.assertEqual(first["context"], "TEST")
        self.assertEqual(first["workflowStatus"], "RETIRED ARCHIVED")
        self.assertEqual(first["registrationStatus"], "Qualified")
        for match in result["matches"]:
            self.assertNotIn("score", match)
            self.assertNotIn("crosswalk", match)
