"""Shared SI contracts: recorded requests and malformed/empty distinctions, offline.

Published https://cadsrapi.cancer.gov/SSISAdvQueries/v1/swagger.yaml (2026-10-06):
graph_names requires limit; with_concept_id requires graph_name, resource_name and
dec_pub_id, all strings. These tests pin those exact request parameters, independently
of the client. The bounded identity template adds LIMIT 3 to the original recording;
its response is reused offline, not represented as a new live recording.
"""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from nci_si_mcp.config import Settings
from nci_si_mcp.errors import InputValidationError, PlatformError
from nci_si_mcp.invocation import call
from nci_si_mcp.ssis import CADSR_GRAPH, NCIT_GRAPH, SSISClient
from test_http_client import Reply, ServerTestCase

FIXTURES = Path(__file__).resolve().parents[1] / "acceptance/fixtures/recorded"


def recording(name):
    return json.loads((FIXTURES / name).read_text())


def sparql_reply(rows):
    return Reply(body=json.dumps({"results": {"bindings": rows}}).encode())


class SSISClientTest(ServerTestCase):
    def ssis(self, server):
        client = SSISClient(Settings(ssis_facade_url=server.url, ssis_sparql_url=server.url))
        client.http.sleep = lambda _: None
        client.sparql_http.sleep = lambda _: None
        return client

    def test_facade_graph_names_include_required_limit_and_json_accept(self):
        fixture = recording("ssis/graph-names.json")
        server = self.serve(Reply(body=json.dumps(fixture["response"]["body"]).encode()))
        result = self.ssis(server).get_graph_names()
        self.assertEqual(result, fixture["response"]["body"]["graph"])
        self.assertEqual(server.seen[0][0], "/si-api/v1/database/graph_names?limit=100")
        self.assertEqual(server.seen[0][1]["accept"], "application/json")

    def test_facade_concept_operation_uses_the_dec_identifier_not_an_ncit_code(self):
        fixture = recording("ssis/data-elements-of-dec-2226947.json")
        server = self.serve(Reply(body=json.dumps(fixture["response"]["body"]).encode()))
        result = self.ssis(server).get_data_elements_for_dec("2226947")
        self.assertEqual(result, fixture["response"]["body"]["results"])
        self.assertEqual(urlsplit(server.seen[0][0]).path, fixture["request"]["path"])
        self.assertEqual(parse_qs(urlsplit(server.seen[0][0]).query), fixture["request"]["params"])
        self.assertEqual(server.seen[0][1]["accept"], "application/json")

    def test_graph_identity_preserves_both_dates_and_optional_version_without_release_check(self):
        fixture = recording("ssis-sparql/graph-identities.json")
        server = self.serve(Reply(body=json.dumps(fixture["response"]["body"]).encode()))
        result = self.ssis(server).get_graph_identities()
        self.assertEqual(
            result,
            [
                {"graph": NCIT_GRAPH, "date": "September 28, 2026", "version": "26.09d"},
                {"graph": CADSR_GRAPH, "date": "2026-07-01"},
            ],
        )
        query = parse_qs(server.bodies[0].decode())["query"][0]
        expected = fixture["request"]["form"]["query"] + "\nLIMIT 3"
        self.assertEqual(query.split(), expected.split())
        self.assertEqual(server.seen[0][1]["content-type"], "application/x-www-form-urlencoded")
        self.assertEqual(server.seen[0][1]["accept"], "application/sparql-results+json")

    def test_content_queries_match_the_recorded_forms_and_preserve_rows(self):
        for name, method, argument, options in (
            ("data-elements-c17357", "find_data_elements", "C17357", {}),
            (
                "data-elements-c17357-descendants",
                "find_data_elements",
                "C17357",
                {"expand_descendants": True},
            ),
            ("values-c20197", "find_permissible_values", "C20197", {}),
            (
                "values-c17357-descendants",
                "find_permissible_values",
                "C17357",
                {"expand_descendants": True},
            ),
        ):
            with self.subTest(name=name):
                fixture = recording(f"ssis-sparql/{name}.json")
                server = self.serve(Reply(body=json.dumps(fixture["response"]["body"]).encode()))
                result = getattr(self.ssis(server), method)(argument, **options)
                rows = fixture["response"]["body"]["results"]["bindings"]
                self.assertEqual(
                    result, [{key: value["value"] for key, value in row.items()} for row in rows]
                )
                query = parse_qs(server.bodies[0].decode())["query"][0]
                self.assertEqual(query.split(), fixture["request"]["form"]["query"].split())
                self.assertNotIn("OPTION", query)

    def test_value_query_keeps_versions_and_values_without_main_concepts(self):
        rows = [
            {"version": {"type": "literal", "value": "2.10"}},
            {
                "version": {"type": "literal", "value": "2.9"},
                "value": {"type": "literal", "value": "Male"},
            },
            {
                "version": {"type": "literal", "value": "2.8"},
                "value": {"type": "literal", "value": ""},
                "concept": {"type": "uri", "value": "http://example.test/C1"},
                "role": {"type": "uri", "value": CADSR_GRAPH + "#main_concept"},
            },
        ]
        server = self.serve(sparql_reply(rows))
        result = self.ssis(server).get_permissible_values("123")
        self.assertEqual(
            result,
            [{key: term["value"] for key, term in row.items()} for row in rows],
        )
        query = parse_qs(server.bodies[0].decode())["query"][0]
        self.assertEqual(query.count("OPTIONAL"), 2)
        self.assertIn("VALUES ?role { cadsr:main_concept }", query)
        self.assertNotIn("minor_concept", query)
        self.assertTrue(query.endswith("LIMIT 1001"))

    def test_value_query_rejects_inconsistent_optional_bindings(self):
        terms = {
            "value": {"type": "literal", "value": "Male"},
            "concept": {"type": "uri", "value": "http://example.test/C1"},
            "role": {"type": "uri", "value": CADSR_GRAPH + "#main_concept"},
        }
        for keys in (
            ("concept",),
            ("role",),
            ("concept", "role"),
            ("value", "concept"),
            ("value", "role"),
        ):
            with self.subTest(keys=keys):
                row = {"version": {"type": "literal", "value": "2.10"}}
                row.update({key: terms[key] for key in keys})
                with self.assertRaises(PlatformError) as raised:
                    self.ssis(self.serve(sparql_reply([row]))).get_permissible_values("123")
                self.assertEqual(raised.exception.code, "upstream_unavailable")

    def test_value_query_rejects_a_role_other_than_main_concept(self):
        row = {
            "version": {"type": "literal", "value": "2.10"},
            "value": {"type": "literal", "value": "Male"},
            "concept": {"type": "uri", "value": "http://example.test/C1"},
            "role": {"type": "uri", "value": CADSR_GRAPH + "#minor_concept"},
        }
        with self.assertRaises(PlatformError) as raised:
            self.ssis(self.serve(sparql_reply([row]))).get_permissible_values("123")
        self.assertEqual(raised.exception.code, "upstream_unavailable")

    def test_maximum_plus_one_retains_the_sentinel_and_rejects_an_unbounded_answer(self):
        fixture = recording("ssis-sparql/data-elements-c17357.json")
        rows = fixture["response"]["body"]["results"]["bindings"]
        server = self.serve(sparql_reply(rows[:3]), sparql_reply(rows[:4]))
        client = self.ssis(server)
        self.assertEqual(len(client.find_data_elements("C17357", maximum=2)), 3)
        with self.assertRaises(PlatformError) as raised:
            client.find_data_elements("C17357", maximum=2)
        self.assertEqual(raised.exception.code, "upstream_unavailable")
        for body in server.bodies:
            self.assertTrue(parse_qs(body.decode())["query"][0].rstrip().endswith("LIMIT 3"))

    def test_empty_lists_are_successful_for_every_operation(self):
        for method, arguments, body in (
            ("get_graph_names", (), {"graph": []}),
            ("get_data_elements_for_dec", ("2226947",), {"results": []}),
            ("find_data_elements", ("C17357",), {"results": {"bindings": []}}),
            ("find_permissible_values", ("C17357",), {"results": {"bindings": []}}),
            ("get_permissible_values", ("2200604",), {"results": {"bindings": []}}),
        ):
            with self.subTest(method=method):
                server = self.serve(Reply(body=json.dumps(body).encode()))
                self.assertEqual(getattr(self.ssis(server), method)(*arguments), [])

    def test_missing_or_malformed_bindings_never_mean_empty(self):
        for body in (
            {},
            {"results": {}},
            {"results": None},
            {"results": {"bindings": None}},
            {"results": {"bindings": [{}]}},
            {"results": {"bindings": ["bad"]}},
        ):
            with self.subTest(body=body):
                server = self.serve(Reply(body=json.dumps(body).encode()))
                with self.assertRaises(PlatformError) as raised:
                    self.ssis(server).find_data_elements("C17357")
                self.assertEqual(raised.exception.code, "upstream_unavailable")

    def test_each_selected_variable_is_required_and_malformed_terms_fail(self):
        original = recording("ssis-sparql/data-elements-c17357.json")["response"]["body"][
            "results"
        ]["bindings"][0]
        for key in ("id", "version", "name"):
            for term in ("missing", None, {}, {"value": "x"}, {"type": "literal", "value": 4}):
                with self.subTest(key=key, term=term):
                    row = copy.deepcopy(original)
                    if term == "missing":
                        del row[key]
                    else:
                        row[key] = term
                    with self.assertRaises(PlatformError) as raised:
                        self.ssis(self.serve(sparql_reply([row]))).find_data_elements("C17357")
                    self.assertEqual(raised.exception.code, "upstream_unavailable")

    def test_missing_facade_results_and_masked_failures_are_not_empty(self):
        for method, arguments, body in (
            ("get_graph_names", (), {}),
            ("get_graph_names", (), {"graph": [3]}),
            ("get_graph_names", (), {"apiResponse": {"type": "E"}}),
            ("get_data_elements_for_dec", ("2226947",), {}),
            ("get_data_elements_for_dec", ("2226947",), {"results": [{}]}),
        ):
            with self.subTest(method=method, body=body):
                client = self.ssis(self.serve(Reply(body=json.dumps(body).encode())))
                with self.assertRaises(PlatformError) as raised:
                    getattr(client, method)(*arguments)
                self.assertEqual(raised.exception.code, "upstream_unavailable")

    def test_invalid_graph_metadata_is_rejected_without_a_fabricated_identity(self):
        original = recording("ssis-sparql/graph-identities.json")["response"]["body"]["results"][
            "bindings"
        ]
        for key, value in (
            ("graph", "https://wrong.example/graph"),
            ("date", "not a date"),
            ("version", ""),
        ):
            with self.subTest(key=key):
                rows = copy.deepcopy(original)
                rows[0][key]["value"] = value
                with self.assertRaises(PlatformError) as raised:
                    self.ssis(self.serve(sparql_reply(rows))).get_graph_identities()
                self.assertEqual(raised.exception.code, "upstream_unavailable")

    def test_partial_duplicate_and_overfull_graph_identities_are_not_silently_used(self):
        original = recording("ssis-sparql/graph-identities.json")["response"]["body"]["results"][
            "bindings"
        ]
        for rows in ([], original[:1], original[:1] * 2, original + original[:1], original * 2):
            with self.subTest(count=len(rows)):
                with self.assertRaises(PlatformError) as raised:
                    self.ssis(self.serve(sparql_reply(rows))).get_graph_identities()
                self.assertEqual(raised.exception.code, "upstream_unavailable")

    def test_both_versions_are_preserved_even_when_different_from_evs(self):
        rows = copy.deepcopy(
            recording("ssis-sparql/graph-identities.json")["response"]["body"]["results"][
                "bindings"
            ]
        )
        rows[0]["version"]["value"] = "26.08e"
        rows[1]["version"] = {"type": "literal", "value": "actual-registry-version"}
        result = self.ssis(self.serve(sparql_reply(rows))).get_graph_identities()
        self.assertEqual([row["version"] for row in result], ["26.08e", "actual-registry-version"])

    def test_html_403_reaches_the_common_error_path_with_one_request(self):
        server = self.serve(Reply(403, body=b"<html>Request blocked</html>"))
        client = self.ssis(server)
        result = call(
            "find_data_elements_for_concept",
            lambda client=client: {"items": client.find_data_elements("C17357")},
        )
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.assertEqual(result["error"]["details"]["status"], 403)
        self.assertNotIn("reason", result["error"]["details"])
        self.assertEqual(len(server.bodies), 1)

    def test_other_http_rejections_keep_the_original_status(self):
        for status in (401, 403):
            with self.subTest(status=status):
                server = self.serve(Reply(status, body=b'{"message":"Access denied"}'))
                client = self.ssis(server)
                result = call(
                    "find_data_elements_for_concept",
                    lambda client=client: {"items": client.find_data_elements("C17357")},
                )
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.assertEqual(result["error"]["details"]["status"], status)
                self.assertNotIn("reason", result["error"]["details"])
                self.assertNotIn("inspection layer", result["error"]["message"])
                self.assertEqual(len(server.bodies), 1)

    def test_ssis_clients_never_send_the_cadsr_credential(self):
        rows = recording("ssis-sparql/graph-identities.json")["response"]["body"]["results"][
            "bindings"
        ]
        server = self.serve(Reply(body=b'{"graph": []}'), sparql_reply(rows))
        client = SSISClient(
            Settings(
                ssis_facade_url=server.url,
                ssis_sparql_url=server.url,
                cadsr_credential="fixture-only:secret",
            )
        )
        self.assertEqual(client.get_graph_names(), [])
        self.assertEqual(
            {row["graph"] for row in client.get_graph_identities()}, {NCIT_GRAPH, CADSR_GRAPH}
        )
        self.assertTrue(all("authorization" not in headers for _, headers in server.seen))


class SSISValidationTest(unittest.TestCase):
    def test_identifiers_rejected_before_any_transport(self):
        client = SSISClient(Settings())
        with patch("nci_si_mcp.http_client._open", side_effect=AssertionError("Unexpected HTTP")):
            for method in (
                client.find_data_elements,
                client.find_permissible_values,
                client.get_permissible_values,
                client.get_data_elements_for_dec,
            ):
                for value in ("", "C17357>", '2200604"', "C17357}", None, 123):
                    with (
                        self.subTest(method=method.__name__, value=value),
                        self.assertRaises(InputValidationError),
                    ):
                        method(value)

    def test_invalid_limits_and_expansion_are_rejected_without_http(self):
        client = SSISClient(Settings())
        with patch("nci_si_mcp.http_client._open", side_effect=AssertionError("Unexpected HTTP")):
            for value in (0, -1, True, 1.5, "2"):
                with self.subTest(value=value):
                    with self.assertRaises(InputValidationError):
                        client.get_graph_names(value)
                    with self.assertRaises(InputValidationError):
                        client.find_data_elements("C17357", maximum=value)
            with self.assertRaises(InputValidationError):
                client.find_data_elements("C17357", expand_descendants="true")
