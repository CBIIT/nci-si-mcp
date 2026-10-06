"""caDSR tool behavior against offline contract-crafted HTTP responses."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

from fakes import data_element as element
from nci_si_mcp.caching import cache_call
from nci_si_mcp.config import Settings
from nci_si_mcp.context import Context
from nci_si_mcp.registry import invoke
from test_cadsr_client import LISTING, reply
from test_http_client import Reply, ServerTestCase


class CaDSRFixture(ServerTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def context(self, server):
        return Context(
            Settings(
                data_dir=Path(self.directory.name),
                cadsr_base_url=server.url,
                cadsr_ftp_url=server.url,
            )
        )

    def call(self, server, operation="get_data_element", **arguments):
        with cache_call() as hint:
            result = invoke(
                self.context(server), operation, _correlation_id="fixture-call", **arguments
            )
        self.hint = hint
        return result


class CaDSRContentTest(CaDSRFixture):
    def test_unpinned_element_preserves_own_fields_without_export_metadata(self):
        raw = element(AlternateNames=[{"name": "Alias"}])
        server = self.serve(reply({"DataElement": raw}))
        result = self.call(server, publicId="123")
        self.assertEqual({k: v for k, v in result.items() if k != "provenance"}, element())
        self.assertEqual(result["provenance"]["release"], {"registry": "cadsr"})
        self.assertEqual(result["provenance"]["correlationId"], "fixture-call")
        self.assertEqual(self.hint, {"ttlMs": 3_600_000, "cacheScope": "public"})
        self.assertEqual([path for path, _ in server.seen], ["/NCIAPI/1.0/api/DataElement/123"])

    def test_unpublished_pin_fails_before_content_access(self):
        server = self.serve(Reply(404))
        result = self.call(server, publicId="123", registryRelease="2026.07.02")
        self.assertEqual(result["error"]["code"], "release_not_available")
        self.assertEqual(result["error"]["details"], {"requested": "2026.07.02", "source": "cadsr"})
        self.assertEqual([path for path, _ in server.seen], ["/NCIAPI/1.0/api/registry/releases"])

    def test_verified_pin_reaches_the_content_request_and_cache_policy(self):
        row = {"identifier": "2026.07.02", "generatedAt": "2026-07-01T22:19", "latest": True}
        server = self.serve(
            reply({"registryReleases": [row]}),
            reply({"DataElement": element(), "registryRelease": row["identifier"]}),
        )
        result = self.call(server, publicId="123", version="2", registryRelease=row["identifier"])
        self.assertEqual(
            parse_qs(urlsplit(server.seen[-1][0]).query),
            {"version": ["2"], "registryRelease": [row["identifier"]], "publicId": ["123"]},
        )
        self.assertEqual(urlsplit(server.seen[-1][0]).path, "/NCIAPI/1.0/api/DataElement")
        self.assertEqual(
            result["provenance"]["release"],
            {"registry": "cadsr", "identifier": row["identifier"], "date": row["generatedAt"]},
        )
        self.assertEqual(self.hint, {"ttlMs": 86_400_000, "cacheScope": "public"})

    def test_question_text_resolves_the_full_item_and_preserves_requested_version(self):
        server = self.serve(
            reply({"DataElements": [{"publicId": "123"}]}), reply({"DataElement": element()})
        )
        result = self.call(server, questionText="A & B", version="2")
        self.assertEqual(result["dateModified"], "2026-08-25")
        self.assertEqual(parse_qs(urlsplit(server.seen[0][0]).query)["documentText"], ["A & B"])
        self.assertEqual(server.seen[1][0], "/NCIAPI/1.0/api/DataElement/123?version=2")

    def test_ambiguous_question_names_candidates_without_fetching_one(self):
        server = self.serve(reply({"DataElements": [{"publicId": "123"}, {"publicId": "456"}]}))
        result = self.call(server, questionText="Shared question")
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertIn("123", json.dumps(result))
        self.assertIn("456", json.dumps(result))
        self.assertEqual(len(server.seen), 1)

    def test_live_shaped_search_error_gives_fixed_actionable_guidance(self):
        server = self.serve(reply({"apiResponse": {"type": "E", "message": "untrusted wording"}}))
        result = self.call(server, "search_data_elements", query="patient")
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        message = result["error"]["message"]
        self.assertIn("OP-C03", message)
        self.assertIn("get_data_element", message)
        self.assertTrue(
            message.endswith(
                "Retrying will not help until caDSR adds it. Use get_data_element by public "
                "id or question text meanwhile."
            )
        )
        self.assertEqual(len(server.seen), 1)

    def test_unsupported_filters_never_reach_upstream(self):
        server = self.serve()
        result = self.call(
            server, "search_data_elements", query="patient", filters={"context": "TEST"}
        )
        self.assertEqual(result["error"]["code"], "capability_unavailable")
        self.assertEqual(server.seen, [])

    def test_search_keeps_the_actual_failure_alongside_the_static_platform_note(self):
        row = {"identifier": "known", "generatedAt": "2026-07-01T22:19"}
        for response, arguments, diagnostic in (
            (Reply(401), {}, "credentials"),
            (reply({"DataElements": [element()], "numRecords": 0}), {}, "malformed search count"),
            (
                reply({"registryReleases": [row, row]}),
                {"registryRelease": "known"},
                "more than once",
            ),
        ):
            with self.subTest(diagnostic=diagnostic):
                server = self.serve(response)
                result = self.call(server, "search_data_elements", query="patient", **arguments)
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.assertIn(diagnostic, result["error"]["message"])
                self.assertIn("OP-C03", result["error"]["message"])
                self.assertIn("get_data_element", result["error"]["message"])
                self.assertEqual(len(server.seen), 1)

    def test_question_search_and_contexts_require_the_verified_pin_echo(self):
        row = {"identifier": "known", "generatedAt": "2026-07-01T22:19"}
        for operation, arguments, payload in (
            ("get_data_element", {"questionText": "Q"}, {"DataElements": [{"publicId": "123"}]}),
            ("search_data_elements", {"query": "Q"}, {"DataElements": [element()]}),
            ("list_contexts", {}, {"contextNames": ["TEST"]}),
        ):
            for echo, served in (
                ({}, []),
                ({"registryRelease": "other"}, ["other"]),
                ({"registryRelease": 42}, []),
                ({"registryRelease": ""}, []),
            ):
                with self.subTest(operation=operation, echo=echo):
                    server = self.serve(reply({"registryReleases": [row]}), reply(payload | echo))
                    result = self.call(server, operation, registryRelease="known", **arguments)
                    self.assertEqual(result["error"]["code"], "release_mismatch")
                    self.assertEqual(
                        result["error"]["details"],
                        {
                            "requested": "known",
                            "served": served,
                            "source": "cadsr",
                        },
                    )
                    self.assertEqual(set(result), {"error"})
                    self.assertEqual(len(server.seen), 2)

    def test_capped_search_continues_without_inventing_a_count(self):
        rows = [element(str(1000 + i)) for i in range(1000)]
        server = self.serve(reply({"DataElements": rows}), reply({"DataElements": rows}))
        first = self.call(server, "search_data_elements", query="patient", limit=3)
        second = self.call(
            server, "search_data_elements", query="patient", limit=3, cursor=first["nextCursor"]
        )
        self.assertEqual(
            [r["dataElement"]["publicId"] for r in second["results"]], ["1003", "1004", "1005"]
        )
        self.assertEqual(
            first["truncation"],
            {
                "occurred": True,
                "bound": "upstream_cap",
                "limit": 1000,
                "reached": 1000,
                "omitted": 1,
                "exact": False,
            },
        )
        self.assertNotIn("totalKnown", first)

    def test_export_registry_tool_is_uncached_and_keeps_local_time(self):
        server = self.serve(Reply(404), Reply(body=LISTING.encode()))
        result = self.call(server, "resolve_registry_release")
        self.assertEqual(
            result,
            {
                "published": False,
                "generatedAt": "2026-07-01T22:19",
                "sourceDistribution": "releasedCDEsXML-OD.zip",
            },
        )
        self.assertEqual(self.hint, {"ttlMs": 0, "cacheScope": "public"})

    def test_each_selected_section_has_the_spec_shape_and_nested_provenance(self):
        raw = element(
            ValueDomain={
                "type": "Enumerated",
                "PermissibleValues": [
                    {
                        "publicId": "800",
                        "value": "A",
                        "ValueMeaning": {
                            "publicId": "801",
                            "version": "1",
                            "longName": "Fixture meaning",
                            "Concepts": [
                                {
                                    "conceptCode": "C1",
                                    "longName": "Alpha",
                                    "primaryIndicator": "Yes",
                                }
                            ],
                        },
                    }
                ],
            },
            ClassificationSchemes=[
                {
                    "publicId": "700",
                    "version": "1",
                    "longName": "Fixture scheme",
                    "context": "TEST",
                    "ClassificationSchemeItems": [
                        {"publicId": "701", "version": "1", "longName": "Item"}
                    ],
                }
            ],
            DataElementConcept={
                "ObjectClass": {"Concepts": [{"conceptCode": "C2", "longName": "Object"}]},
                "Property": {"Concepts": [{"conceptCode": "C3", "longName": "Property"}]},
            },
            AlternateNames=[{"name": "Verbatim alias", "type": "Fixture"}],
        )
        expected = {
            "valueDomain": {"type": "Enumerated"},
            "alternateNames": raw["AlternateNames"],
            "conceptAssociations": [
                {"conceptCode": "C2", "longName": "Object", "role": "objectClass"},
                {"conceptCode": "C3", "longName": "Property", "role": "property"},
            ],
            "permissibleValues": [
                {
                    "publicId": "800",
                    "value": "A",
                    "valueMeaning": {
                        "publicId": "801",
                        "version": "1",
                        "longName": "Fixture meaning",
                        "concepts": [{"conceptCode": "C1", "longName": "Alpha", "primary": True}],
                    },
                }
            ],
            "classificationSchemes": [
                {
                    "publicId": "700",
                    "version": "1",
                    "longName": "Fixture scheme",
                    "context": "TEST",
                    "items": [{"publicId": "701", "version": "1", "longName": "Item"}],
                }
            ],
        }
        for section, value in expected.items():
            with self.subTest(section=section):
                server = self.serve(reply({"DataElement": raw}))
                result = self.call(server, publicId="123", include=[section, section])
                self.assertEqual(set(result) & set(expected), {section})
                actual = result[section]
                if section in ("permissibleValues", "classificationSchemes"):
                    self.assertEqual(actual[0].pop("provenance")["release"], {"registry": "cadsr"})
                self.assertEqual(actual, value)

    def test_invalid_inputs_fail_before_any_http_request(self):
        for args in (
            {},
            {"publicId": "123", "questionText": "Q"},
            {"publicId": ""},
            {"publicId": "1/2"},
            {"questionText": " "},
            {"publicId": "123", "version": "x"},
            {"publicId": "123", "include": ["unknown"]},
            {"publicId": "123", "registryRelease": " "},
        ):
            with self.subTest(args=args):
                server = self.serve()
                result = self.call(server, **args)
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(server.seen, [])

    def test_known_unavailable_capabilities_make_no_content_request(self):
        for tool, args in (
            ("get_data_element", {"longName": "Name"}),
            ("list_classification_schemes", {"context": "TEST"}),
            ("search_data_elements", {"query": "Q", "mode": "semantic"}),
            ("search_data_elements", {"query": "Q", "mode": "hybrid"}),
        ):
            with self.subTest(tool=tool, args=args):
                server = self.serve()
                result = self.call(server, tool, **args)
                self.assertEqual(result["error"]["code"], "capability_unavailable")
                self.assertEqual(server.seen, [])

    def test_informational_absence_and_no_question_candidates_are_not_found(self):
        for body, args in (
            ({"DataElement": None, "apiResponse": {"type": "I"}}, {"publicId": "123"}),
            ({"DataElements": []}, {"questionText": "Nothing"}),
        ):
            with self.subTest(args=args):
                result = self.call(self.serve(reply(body)), **args)
                self.assertEqual(result["error"]["code"], "not_found")
                self.assertEqual(result["error"]["details"], {"identifiers": list(args.values())})

    def test_wrong_item_identity_or_version_is_never_returned(self):
        for raw in (
            element("456"),
            element(version="3"),
            element(version=None),
            element(workflowStatus=[]),
        ):
            with self.subTest(raw=raw):
                result = self.call(
                    self.serve(reply({"DataElement": raw})), publicId="123", version="2"
                )
                self.assertEqual(result["error"]["code"], "upstream_unavailable")

    def test_malformed_question_candidates_are_upstream_failures(self):
        for row in ({}, {"publicId": "0"}, {"publicId": 123}):
            with self.subTest(row=row):
                result = self.call(self.serve(reply({"DataElements": [row]})), questionText="Q")
                self.assertEqual(result["error"]["code"], "upstream_unavailable")

    def test_malformed_requested_sections_are_errors_not_empty_results(self):
        for extra, section in (
            ({"AlternateNames": None}, "alternateNames"),
            ({"ValueDomain": None}, "valueDomain"),
            ({"ClassificationSchemes": [1]}, "classificationSchemes"),
            (
                {"ValueDomain": {"PermissibleValues": [{"ValueMeaning": {"Concepts": [{}]}}]}},
                "permissibleValues",
            ),
        ):
            with self.subTest(section=section):
                result = self.call(
                    self.serve(reply({"DataElement": element(**extra)})),
                    publicId="123",
                    include=[section],
                )
                self.assertEqual(result["error"]["code"], "upstream_unavailable")

    def test_unlisted_or_duplicate_pins_never_request_content(self):
        row = {"identifier": "known", "generatedAt": "2026-01-01"}
        for rows, requested, code in (
            ([row], "other", "release_not_available"),
            ([row, row], "known", "upstream_unavailable"),
        ):
            with self.subTest(requested=requested):
                server = self.serve(reply({"registryReleases": rows}))
                result = self.call(server, publicId="123", registryRelease=requested)
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(len(server.seen), 1)

    def test_a_verified_pin_missing_from_content_is_not_claimed_in_provenance(self):
        row = {"identifier": "known", "generatedAt": "2026-01-01"}
        for returned in (None, "different"):
            with self.subTest(returned=returned):
                server = self.serve(
                    reply({"registryReleases": [row]}),
                    reply({"DataElement": element(), "registryRelease": returned}),
                )
                result = self.call(server, publicId="123", registryRelease="known")
                self.assertEqual(result["error"]["code"], "release_mismatch")

    def test_empty_search_has_provenance_and_only_an_upstream_count(self):
        for body, counted in (
            ({"DataElements": []}, False),
            ({"DataElements": [], "numRecords": 0}, True),
        ):
            with self.subTest(counted=counted):
                result = self.call(self.serve(reply(body)), "search_data_elements", query="none")
                self.assertEqual(result["results"], [])
                self.assertEqual(result["provenance"]["release"], {"registry": "cadsr"})
                self.assertEqual(result["truncation"], {"occurred": False})
                self.assertEqual("totalKnown" in result, counted)
                self.assertEqual(self.hint["ttlMs"], 3_600_000)

    def test_malformed_or_incomplete_search_count_fails(self):
        for count in (True, "1", -1, 2):
            with self.subTest(count=count):
                server = self.serve(reply({"DataElements": [element()], "numRecords": count}))
                result = self.call(server, "search_data_elements", query="Q")
                self.assertEqual(result["error"]["code"], "upstream_unavailable")

    def test_search_cursor_binds_query_limit_mode_filters_and_pin(self):
        server = self.serve(reply({"DataElements": [element(), element("456")]}))
        first = self.call(server, "search_data_elements", query="Q", limit=1)
        base = {"query": "Q", "limit": 1, "cursor": first["nextCursor"]}
        for changed in (
            {"query": "other"},
            {"limit": 2},
            {"mode": "semantic"},
            {"filters": {"context": "TEST"}},
            {"registryRelease": "known"},
        ):
            with self.subTest(changed=changed):
                result = self.call(server, "search_data_elements", **(base | changed))
                self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(len(server.seen), 1)

    def test_context_paging_keeps_names_and_never_adds_definitions(self):
        server = self.serve(
            reply({"contextNames": ["B", "A"]}), reply({"contextNames": ["B", "A"]})
        )
        first = self.call(server, "list_contexts", limit=1)
        second = self.call(server, "list_contexts", limit=1, cursor=first["nextCursor"])
        self.assertEqual([first["contexts"][0]["name"], second["contexts"][0]["name"]], ["B", "A"])
        self.assertEqual(set(second["contexts"][0]), {"name", "provenance"})
        self.assertNotIn("nextCursor", second)

    def test_empty_context_list_is_short_lived_and_has_provenance(self):
        result = self.call(self.serve(reply({"contextNames": []})), "list_contexts")
        self.assertEqual(result["contexts"], [])
        self.assertEqual(result["provenance"]["release"], {"registry": "cadsr"})
        self.assertEqual(self.hint["ttlMs"], 3_600_000)

    def test_every_request_of_pinned_question_lookup_names_the_registry(self):
        row = {"identifier": "known", "generatedAt": "2026-01-01"}
        server = self.serve(
            reply({"registryReleases": [row]}),
            reply({"DataElements": [{"publicId": "123"}], "registryRelease": "known"}),
            reply({"DataElement": element(), "registryRelease": "known"}),
        )
        with self.assertLogs("nci_si_mcp", level="INFO") as logs:
            result = self.call(server, questionText="Q", registryRelease="known")
        self.assertEqual(result["publicId"], "123")
        self.assertEqual(
            [parse_qs(urlsplit(path).query)["registryRelease"] for path, _ in server.seen[1:]],
            [["known"], ["known"]],
        )
        completed = next(
            record.structured
            for record in logs.records
            if record.structured["event"] == "call_completed"
        )
        self.assertEqual(completed["release"]["requested"], "known")
        self.assertEqual(completed["outboundRequests"], 3)

    def test_pinned_search_and_context_list_send_and_verify_the_pin(self):
        row = {"identifier": "known", "generatedAt": "2026-01-01"}
        for tool, args, body, key in (
            ("search_data_elements", {"query": "Q"}, {"DataElements": []}, "results"),
            ("list_contexts", {}, {"contextNames": []}, "contexts"),
        ):
            with self.subTest(tool=tool):
                server = self.serve(
                    reply({"registryReleases": [row]}), reply(body | {"registryRelease": "known"})
                )
                result = self.call(server, tool, **args, registryRelease="known")
                self.assertEqual(result[key], [])
                self.assertEqual(result["provenance"]["release"]["identifier"], "known")
                self.assertEqual(
                    parse_qs(urlsplit(server.seen[-1][0]).query)["registryRelease"], ["known"]
                )
                self.assertEqual(self.hint["ttlMs"], 86_400_000)

    def test_invalid_search_options_fail_without_a_request(self):
        for args in (
            {"query": " "},
            {"query": "Q", "filters": []},
            {"query": "Q", "filters": {"context": []}},
            {"query": "Q", "filters": {"other": "x"}},
            {"query": "Q", "mode": "other"},
        ):
            with self.subTest(args=args):
                server = self.serve()
                result = self.call(server, "search_data_elements", **args)
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(server.seen, [])

    def test_invalid_search_identity_is_an_upstream_error_not_returned_content(self):
        server = self.serve(reply({"DataElements": [element(version="wrong")]}))
        result = self.call(server, "search_data_elements", query="Q")
        self.assertEqual(result["error"]["code"], "upstream_unavailable")

    def test_upstream_attribution_passes_through_without_local_licence_data(self):
        raw = element(licenseText="Fixture attribution supplied by the platform")
        result = self.call(self.serve(reply({"DataElement": raw})), publicId="123")
        self.assertEqual(result["provenance"]["attribution"], raw["licenseText"])

    def test_refused_cadsr_access_names_credentials_without_an_evs_remedy(self):
        result = self.call(self.serve(Reply(401)), "list_contexts")
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.assertIn("credentials", result["error"]["message"])
        self.assertNotIn("EVS", result["error"]["message"])

    def test_value_meaning_primary_is_upstream_fact_and_malformed_values_fail(self):
        for value, code, primary, valid in (
            ("A", "C1", "No", True),
            (None, "C1", "Yes", False),
            ("A", None, "Yes", False),
            ("A", "C1", None, False),
        ):
            with self.subTest(value=value, code=code, primary=primary):
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
                            {"publicId": "800", "value": value, "ValueMeaning": meaning},
                        ]
                    }
                )
                result = self.call(
                    self.serve(reply({"DataElement": raw})),
                    publicId="123",
                    include=["permissibleValues"],
                )
                if valid:
                    self.assertIs(
                        result["permissibleValues"][0]["valueMeaning"]["concepts"][0]["primary"],
                        False,
                    )
                else:
                    self.assertEqual(result["error"]["code"], "upstream_unavailable")
