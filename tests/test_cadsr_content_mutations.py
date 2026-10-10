"""Content boundaries and provenance pinned by the Phase 3 mutation review."""

from urllib.parse import parse_qs, urlsplit

from fakes import data_element as element
from nci_si_mcp import cursor
from nci_si_mcp.cadsr import EXPORT_FOLDER
from nci_si_mcp.registry import invoke
from test_cadsr_client import LISTING, reply
from test_cadsr_content import CaDSRFixture
from test_cadsr_forms import code_map, form
from test_http_client import Reply

PIN = {"identifier": "26.1", "generatedAt": "2026-07-01T22:19"}


class ContentMutationTest(CaDSRFixture):
    def test_versioned_resource_retrieves_named_version_and_records_its_uri(self):
        server = self.serve(reply({"DataElement": element(version="1.5")}))
        result = self.call(server, "data_element_version_resource", publicId="123", version="1.5")
        self.assertEqual(result["version"], "1.5")
        self.assertEqual(server.seen[0][0], "/NCIAPI/1.0/api/DataElement/123?version=1.5")
        self.assertEqual(result["provenance"]["sourceUri"], server.url + server.seen[0][0])

    def test_invalid_arguments_precede_registry_discovery_for_every_content_tool(self):
        cases = (
            ("get_data_element", {}, "publicId"),
            ("get_data_element", {"publicId": "123", "questionText": "Q"}, "publicId"),
            ("get_data_element", {"publicId": ""}, "publicId"),
            ("get_data_element", {"publicId": "1/2"}, "publicId"),
            ("get_data_element", {"questionText": " "}, "questionText"),
            ("get_data_element", {"publicId": "0"}, "publicId"),
            ("get_data_element", {"publicId": 123}, "publicId"),
            ("get_data_element", {"questionText": 123}, "questionText"),
            ("get_data_element", {"publicId": "123", "version": "1.x"}, "version"),
            ("get_data_element", {"publicId": "123", "include": ["bad"]}, "include"),
            ("search_data_elements", {"query": "", "mode": "semantic"}, "query"),
            ("search_data_elements", {"query": "Q", "limit": 0}, "limit"),
            ("search_data_elements", {"query": "Q", "cursor": "bad"}, "cursor"),
            ("list_contexts", {"limit": 0}, "limit"),
            ("list_contexts", {"cursor": "bad"}, "cursor"),
            ("list_classification_schemes", {"limit": 0}, "limit"),
            ("list_classification_schemes", {"cursor": "bad"}, "cursor"),
            ("get_form", {"publicId": "123", "version": "1.x"}, "version"),
            ("get_form", {"publicId": "123", "keyword": "Q"}, "keyword"),
            ("get_permissible_value", {"permissibleValueId": "0"}, "permissibleValueId"),
            ("get_code_map", {"dataElementId": "0"}, "dataElementId"),
            ("get_code_map", {"limit": 0}, "limit"),
            ("get_code_map", {"cursor": "bad"}, "cursor"),
        )
        for tool, args, parameter in cases:
            with self.subTest(tool=tool, args=args):
                server = self.serve(reply({"registryReleases": []}))
                result = self.call(server, tool, registryRelease="unlisted", **args)
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(result["error"]["details"]["parameter"], parameter)
                self.assertEqual(server.seen, [])

    def test_classification_context_is_validated_before_pin_and_capability(self):
        for value in ("", " ", 2, []):
            with self.subTest(value=value):
                server = self.serve(reply({"registryReleases": []}))
                result = self.call(
                    server, "list_classification_schemes", context=value, registryRelease="unknown"
                )
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(result["error"]["details"]["parameter"], "context")
                self.assertEqual(server.seen, [])

    def test_unlisted_pin_precedes_unavailable_capability(self):
        for tool, args in (
            ("search_data_elements", {"query": "Q", "mode": "semantic"}),
            ("list_classification_schemes", {"context": "TEST"}),
        ):
            with self.subTest(tool=tool):
                server = self.serve(reply({"registryReleases": []}))
                result = self.call(server, tool, registryRelease="unlisted", **args)
                self.assertEqual(result["error"]["code"], "release_not_available")
                self.assertEqual(len(server.seen), 1)

    def test_blank_and_nontext_pins_are_invalid_without_discovery(self):
        for pin in ("", 26, []):
            with self.subTest(pin=pin):
                server = self.serve(reply({"DataElement": element()}))
                result = self.call(server, publicId="123", registryRelease=pin)
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(result["error"]["details"]["parameter"], "registryRelease")
                self.assertEqual(server.seen, [])

    def test_pin_identity_is_exact_and_case_sensitive(self):
        for listed, requested in (("26.1", "26"), ("Release", "release")):
            with self.subTest(listed=listed):
                server = self.serve(reply({"registryReleases": [PIN | {"identifier": listed}]}))
                result = self.call(server, publicId="123", registryRelease=requested)
                self.assertEqual(result["error"]["code"], "release_not_available")
                self.assertEqual(result["error"]["details"]["requested"], requested)
                self.assertEqual(len(server.seen), 1)

    def test_question_versions_are_one_candidate_and_text_is_verbatim(self):
        server = self.serve(
            reply(
                {
                    "DataElements": [
                        {"publicId": "123", "version": "1"},
                        {"publicId": "123", "version": "2"},
                    ]
                }
            ),
            reply({"DataElement": element()}),
        )
        result = self.call(server, questionText="  A & B  ")
        self.assertNotIn("error", result)
        self.assertEqual(result["publicId"], "123")
        self.assertEqual(parse_qs(urlsplit(server.seen[0][0]).query)["documentText"], ["  A & B  "])

    def test_ambiguous_question_names_the_argument(self):
        server = self.serve(reply({"DataElements": [{"publicId": "123"}, {"publicId": "124"}]}))
        result = self.call(server, questionText="Q")
        self.assertEqual(result["error"]["details"]["parameter"], "questionText")

    def test_multiple_requested_sections_are_all_returned(self):
        raw = element(AlternateNames=[{"name": "Alias"}], ValueDomain={"type": "Text"})
        result = self.call(
            self.serve(reply({"DataElement": raw})),
            publicId="123",
            include=["alternateNames", "valueDomain"],
        )
        self.assertTrue({"alternateNames", "valueDomain"}.issubset(result))
        self.assertEqual(result["alternateNames"], [{"name": "Alias"}])
        self.assertEqual(result["valueDomain"], {"type": "Text"})

    def test_invalid_nested_concepts_fail_with_cadsr_surface(self):
        for code, primary in (("C1", "Maybe"), (" ", "Yes")):
            with self.subTest(code=code, primary=primary):
                meaning = {
                    "publicId": "801",
                    "version": "1",
                    "longName": "Meaning",
                    "Concepts": [
                        {"conceptCode": code, "longName": "Concept", "primaryIndicator": primary}
                    ],
                }
                raw = element(
                    ValueDomain={
                        "PermissibleValues": [
                            {"publicId": "800", "value": "A", "ValueMeaning": meaning}
                        ]
                    }
                )
                result = self.call(
                    self.serve(reply({"DataElement": raw})),
                    publicId="123",
                    include=["permissibleValues"],
                )
                self.assertIn("error", result)
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.assertEqual(result["error"]["details"]["surface"], "cadsr")

    def test_nonobject_domain_fails_with_cadsr_surface(self):
        result = self.call(
            self.serve(reply({"DataElement": element(ValueDomain=[])})),
            publicId="123",
            include=["valueDomain"],
        )
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.assertEqual(result["error"]["details"]["surface"], "cadsr")

    def test_pinned_content_provenance_records_source_uri_and_modified_date(self):
        cases = (
            (
                "get_data_element",
                {"publicId": "123", "version": "2"},
                {"DataElement": element()},
                None,
            ),
            ("search_data_elements", {"query": "Q"}, {"DataElements": [element()]}, "results"),
            ("list_contexts", {}, {"contextNames": ["TEST"]}, "contexts"),
            ("get_code_map", {}, {"CRDCDataElements": [code_map()]}, "codeMaps"),
        )
        for tool, args, payload, key in cases:
            with self.subTest(tool=tool):
                server = self.serve(
                    reply({"registryReleases": [PIN]}),
                    reply(payload | {"registryRelease": PIN["identifier"]}),
                )
                result = self.call(server, tool, registryRelease=PIN["identifier"], **args)
                item = result if key is None else result[key][0]
                item = item.get("dataElement", item) if key == "results" else item
                provenance = item["provenance"]
                self.assertEqual(provenance["source"], "cadsr_rest")
                self.assertEqual(provenance["sourceUri"], server.url + server.seen[-1][0])
                self.assertEqual(
                    parse_qs(urlsplit(provenance["sourceUri"]).query)["registryRelease"], ["26.1"]
                )
                if key in (None, "results"):
                    self.assertEqual(
                        provenance["upstream"],
                        {
                            "publicId": "123",
                            "version": "2",
                            "dateModified": element()["dateModified"],
                        },
                    )
                if key is not None:
                    self.assertNotIn("provenance", result)

    def test_form_provenance_uri_keeps_version(self):
        server = self.serve(reply({"form": form()}))
        result = self.call(server, "get_form", publicId="123", version="2")
        self.assertEqual(result["provenance"]["source"], "cadsr_rest")
        self.assertEqual(result["provenance"]["sourceUri"], server.url + server.seen[0][0])
        self.assertEqual(
            parse_qs(urlsplit(result["provenance"]["sourceUri"]).query), {"version": ["2"]}
        )

    def test_export_resource_provenance_uses_export_folder(self):
        server = self.serve(reply({"registryReleases": []}))
        export = self.serve(Reply(body=LISTING.encode()))
        context = self.context(server)
        context.cadsr.export_http.base_url = export.url
        result = invoke(context, "registry_resource")
        self.assertEqual(result["provenance"]["source"], "cadsr_export")
        self.assertEqual(result["provenance"]["sourceUri"], export.url + EXPORT_FOLDER)

    def test_capability_details_name_the_unserved_operation(self):
        for tool, args, expected in (
            ("get_data_element", {"longName": "Name"}, "longName lookup (OP-C02)"),
            (
                "search_data_elements",
                {"query": "Q", "mode": "semantic"},
                "semantic data-element search (OP-C04)",
            ),
            ("get_form", {"publicId": "123", "registryRelease": "26.1"}, "pinned form lookup"),
        ):
            with self.subTest(tool=tool):
                server = self.serve(reply({"registryReleases": [PIN]}))
                result = self.call(server, tool, **args)
                self.assertEqual(result["error"]["code"], "capability_unavailable")
                self.assertEqual(result["error"]["details"]["capability"], expected)

    def test_every_unpinnable_capability_refuses_a_published_pin_in_one_wording(self):
        refusals = {}
        for tool, args in (
            ("get_form", {"publicId": "123"}),
            ("match_data_elements", {"entities": [{"name": "Q"}]}),
            ("match_value_meanings", {"values": ["Q"]}),
            ("ground_value", {"conceptCode": "C1"}),
        ):
            server = self.serve(reply({"registryReleases": [PIN]}))
            error = self.call(server, tool, registryRelease="26.1", **args)["error"]
            self.assertEqual(error["code"], "capability_unavailable")
            refusals[tool] = error["message"].replace(error["details"]["capability"], "<it>")
        self.assertEqual(len(set(refusals.values())), 1, refusals)
        self.assertIn("C-1", refusals["get_form"])
        self.assertIn("Omit registryRelease", refusals["get_form"])

    def test_search_page_size_caps_at_one_hundred(self):
        rows = [element(str(i + 1)) for i in range(102)]
        for limit in (100, 101):
            with self.subTest(limit=limit):
                result = self.call(
                    self.serve(reply({"DataElements": rows})),
                    "search_data_elements",
                    query="Q",
                    limit=limit,
                )
                self.assertEqual(len(result["results"]), 100)
                self.assertIn("nextCursor", result)

    def test_search_cap_boundaries_counts_and_final_page(self):
        for length, count, capped in ((999, 999, False), (1000, 2500, True), (1001, 2500, True)):
            with self.subTest(length=length):
                rows = [element(str(i + 1)) for i in range(length)]
                args = {
                    "tool": "search_data_elements",
                    "query": "Q",
                    "mode": "lexical",
                    "filters": {},
                    "limit": 100,
                    "registryRelease": None,
                }
                result = self.call(
                    self.serve(reply({"DataElements": rows, "numRecords": count})),
                    "search_data_elements",
                    query="Q",
                    limit=100,
                    cursor=cursor.encode(args, 900),
                )
                self.assertEqual(len(result["results"]), min(length, 1000) - 900)
                self.assertNotIn("nextCursor", result)
                self.assertEqual(result["totalKnown"], count)
                expected = (
                    {"occurred": False}
                    if not capped
                    else {
                        "occurred": True,
                        "bound": "upstream_cap",
                        "limit": 1000,
                        "reached": 1000,
                        "omitted": 1500,
                        "exact": False,
                    }
                )
                self.assertEqual(result["truncation"], expected)

    def test_count_below_received_rows_is_a_malformed_response(self):
        result = self.call(
            self.serve(reply({"DataElements": [element(), element("124")], "numRecords": 1})),
            "search_data_elements",
            query="Q",
        )
        self.assertEqual(result["error"]["code"], "upstream_unavailable")

    def test_context_page_limit_is_one_thousand_and_cursor_binds_pin_and_limit(self):
        server = self.serve(reply({"contextNames": [str(i) for i in range(1001)]}))
        result = self.call(server, "list_contexts", limit=1001)
        self.assertEqual(len(result["contexts"]), 1000)
        for change in ({"limit": 999}, {"registryRelease": "26.1"}):
            with self.subTest(change=change):
                changed = self.call(
                    server,
                    "list_contexts",
                    **({"limit": 1000, "cursor": result["nextCursor"]} | change),
                )
                self.assertEqual(changed["error"]["code"], "invalid_request")
        self.assertEqual(len(server.seen), 1)

    def test_classification_cursor_binds_context_and_clamped_limit(self):
        args = {
            "tool": "list_classification_schemes",
            "context": "TEST",
            "limit": 1000,
            "registryRelease": None,
        }
        token = cursor.encode(args, 1000)
        server = self.serve()
        result = self.call(
            server, "list_classification_schemes", context="TEST", limit=1001, cursor=token
        )
        self.assertEqual(result["error"]["code"], "capability_unavailable")
        result = self.call(
            server, "list_classification_schemes", context="OTHER", limit=1000, cursor=token
        )
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(server.seen, [])

    def test_code_map_single_unmapped_value_has_zero_coverage(self):
        raw = code_map(permissibleValues=[{"Permissible Value": "A", "Concept Code": ""}])
        result = self.call(self.serve(reply({"CRDCDataElements": [raw]})), "get_code_map")[
            "codeMaps"
        ][0]
        self.assertEqual(result["crdcName"], "Map")
        self.assertIs(result["valueLevelBinding"], True)
        self.assertEqual(result["coverage"], 0)
        self.assertEqual(result["values"], [{"value": "A"}])

    def test_malformed_code_map_fields_fail_instead_of_returning_content(self):
        for changes in (
            {"permissibleValues": [{"Concept Code": "C1"}]},
            {"CDE Public ID": "0"},
            {"Version": "1.x"},
            {"permissibleValues": {}},
            {"CRDC Name": 1},
            {"Used By": []},
        ):
            with self.subTest(changes=changes):
                result = self.call(
                    self.serve(reply({"CRDCDataElements": [code_map(**changes)]})), "get_code_map"
                )
                self.assertIn("error", result)
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.assertEqual(result["error"]["details"]["surface"], "cadsr")

    def test_code_map_limits_follow_spec_and_filters_are_exact(self):
        rows = [code_map(str(i + 1)) for i in range(1001)]
        for limit, expected in ((101, 101), (1000, 1000), (1001, 1000)):
            with self.subTest(limit=limit):
                result = self.call(
                    self.serve(reply({"CRDCDataElements": rows})), "get_code_map", limit=limit
                )
                self.assertEqual(len(result["codeMaps"]), expected)
        for target, identifier, expected in (("cip", "12", []), ("CIP", "12", ["12"])):
            with self.subTest(target=target):
                result = self.call(
                    self.serve(reply({"CRDCDataElements": [code_map("12"), code_map("123")]})),
                    "get_code_map",
                    targetContext=target,
                    dataElementId=identifier,
                )
                self.assertEqual(
                    [row["dataElement"]["publicId"] for row in result["codeMaps"]], expected
                )

    def test_crosswalk_exact_cap_is_complete_and_only_empty_has_top_provenance(self):
        for size in (0, 1000):
            with self.subTest(size=size):
                result = self.call(
                    self.serve(
                        reply({"CRDCDataElements": [code_map(str(i + 1)) for i in range(size)]})
                    ),
                    "crosswalk_resource",
                )
                self.assertEqual(len(result["codeMaps"]), size)
                self.assertNotIn("truncation", result)
                self.assertEqual("provenance" in result, size == 0)
