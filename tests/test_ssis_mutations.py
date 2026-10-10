"""Regression checks for the client contracts exercised by the milestone review."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

from nci_si_mcp.config import Settings
from nci_si_mcp.errors import InputValidationError, PlatformError
from nci_si_mcp.http_client import UpstreamTimeoutError, UpstreamTooLargeError
from nci_si_mcp.ssis import CADSR_GRAPH, SSISClient
from test_evs_client import FakeResponse
from test_http_client import Reply, ServerTestCase
from test_ssis_client import recording, sparql_reply


class SSISContractsTest(ServerTestCase):
    def ssis(self, server):
        return SSISClient(Settings(ssis_sparql_url=server.url))

    def test_identity_requires_each_graph_and_date_field(self):
        original = recording("ssis-sparql/graph-identities.json")["response"]["body"]["results"][
            "bindings"
        ]
        for key in ("graph", "date"):
            with self.subTest(key=key):
                rows = copy.deepcopy(original)
                del rows[0][key]
                with self.assertRaises(PlatformError) as raised:
                    self.ssis(self.serve(sparql_reply(rows))).get_graph_identities()
                self.assertEqual(raised.exception.code, "upstream_unavailable")

    def test_identity_preserves_version_spacing_and_date_case(self):
        rows = recording("ssis-sparql/graph-identities.json")["response"]["body"]["results"][
            "bindings"
        ]
        rows[0]["version"]["value"] = " 26.09D "
        rows[0]["date"]["value"] = "sEPTEMBER 28, 2026"
        rows[0]["date"]["type"] = "typed-literal"
        result = self.ssis(self.serve(sparql_reply(rows))).get_graph_identities()
        self.assertEqual(result[0]["version"], " 26.09D ")
        self.assertEqual(result[0]["date"], "sEPTEMBER 28, 2026")

    def test_blank_version_and_blank_node_are_malformed(self):
        original = recording("ssis-sparql/graph-identities.json")["response"]["body"]["results"][
            "bindings"
        ]
        for key, term in (
            ("version", {"type": "literal", "value": " \t "}),
            ("version", {"type": "bnode", "value": "26.09d"}),
        ):
            with self.subTest(term=term):
                rows = copy.deepcopy(original)
                rows[0][key] = term
                with self.assertRaises(PlatformError) as raised:
                    self.ssis(self.serve(sparql_reply(rows))).get_graph_identities()
                self.assertEqual(raised.exception.code, "upstream_unavailable")

    def test_value_operations_accept_sentinel_and_clamp_query_bounds(self):
        for method, argument in (
            ("find_permissible_values", "C17357"),
            ("get_permissible_values", "2200604"),
        ):
            with self.subTest(method=method):
                self.assert_value_bounds(method, argument)

    def assert_value_bounds(self, method, argument):
        row = {
            "id": {"type": "literal", "value": "2200604"},
            "version": {"type": "literal", "value": "4"},
            "value": {"type": "literal", "value": "Male"},
            "concept": {"type": "uri", "value": "http://example.test/C1"},
            "role": {"type": "uri", "value": CADSR_GRAPH + "#main_concept"},
        }
        server = self.serve(sparql_reply([row] * 3), sparql_reply([]), sparql_reply([row] * 4))
        operation = getattr(self.ssis(server), method)
        self.assertEqual(len(operation(argument, maximum=2)), 3)
        self.assertEqual(operation(argument, maximum=1002), [])
        with self.assertRaises(PlatformError) as raised:
            operation(argument, maximum=2)
        self.assertEqual(raised.exception.code, "upstream_unavailable")
        queries = [parse_qs(body.decode())["query"][0] for body in server.bodies]
        self.assertEqual(
            [query.split()[-2:] for query in queries],
            [["LIMIT", "3"], ["LIMIT", "1001"], ["LIMIT", "3"]],
        )
        self.assertTrue(all(f"GRAPH <{CADSR_GRAPH}>" in query for query in queries))
        self.assertEqual([path for path, _ in server.seen], ["/sparql"] * 3)

    def test_find_value_requires_concept_even_though_optional_value_read_does_not(self):
        row = {
            "id": {"type": "literal", "value": "2200604"},
            "version": {"type": "literal", "value": "4"},
            "value": {"type": "literal", "value": "Male"},
        }
        server = self.serve(sparql_reply([row]), sparql_reply([row]))
        client = self.ssis(server)
        with self.assertRaises(PlatformError) as raised:
            client.find_permissible_values("C17357")
        self.assertEqual(raised.exception.code, "upstream_unavailable")
        self.assertEqual(
            client.get_permissible_values("2200604"),
            [{"id": "2200604", "version": "4", "value": "Male"}],
        )

    def test_optional_value_query_matches_the_approved_derived_request(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "acceptance/fixtures/crafted/ssis-sparql/values-of-2200604-optional.json"
        )
        fixture = json.loads(path.read_text())
        server = self.serve(Reply(body=json.dumps(fixture["response"]["body"]).encode()))
        result = self.ssis(server).get_permissible_values("2200604")
        self.assertEqual(len(result), len(fixture["response"]["body"]["results"]["bindings"]))
        query = parse_qs(server.bodies[0].decode())["query"][0]
        self.assertEqual(query.split(), fixture["request"]["form"]["query"].split())


class SSISValidationContractsTest(unittest.TestCase):
    def test_identifiers_and_expansion_name_the_bad_parameter_before_http(self):
        client = SSISClient(Settings())
        cases = [
            (client.find_data_elements, "C", {}, "conceptCode"),
            (client.find_data_elements, "C0", {}, "conceptCode"),
            (client.find_permissible_values, "C0", {}, "conceptCode"),
            (client.find_permissible_values, "17357", {}, "conceptCode"),
            (client.get_permissible_values, "0", {}, "publicId"),
            (client.get_permissible_values, "007", {}, "publicId"),
            (client.find_data_elements, "C1", {"expand_descendants": 1}, "expandDescendants"),
            (client.find_data_elements, "C1", {"expand_descendants": "true"}, "expandDescendants"),
            (client.find_permissible_values, "C1", {"expand_descendants": 1}, "expandDescendants"),
        ]
        with patch("nci_si_mcp.http_client._open", side_effect=AssertionError("Unexpected HTTP")):
            for operation, argument, options, parameter in cases:
                with self.subTest(operation=operation.__name__, argument=argument):
                    with self.assertRaises(InputValidationError) as raised:
                        operation(argument, **options)
                    self.assertEqual(raised.exception.details["parameter"], parameter)

    def test_every_value_operation_validates_maximum(self):
        client = SSISClient(Settings())
        for operation, argument in (
            (client.find_data_elements, "C1"),
            (client.get_permissible_values, "123"),
            (client.find_permissible_values, "C1"),
        ):
            for maximum in (0, -1, True, 1.5, "2"):
                with (
                    self.subTest(operation=operation.__name__, maximum=maximum),
                    patch("nci_si_mcp.http_client._open", side_effect=AssertionError("HTTP")),
                    self.assertRaises(InputValidationError) as raised,
                ):
                    operation(argument, maximum=maximum)
                    self.assertEqual(raised.exception.details["parameter"], "maximum")

    def test_host_and_timeout_reach_the_transport(self):
        client = SSISClient(
            Settings(
                ssis_sparql_url="https://sparql.example",
                timeout_seconds=17,
            )
        )
        with patch("nci_si_mcp.http_client._open") as transport:
            transport.return_value = FakeResponse(b'{"results":{"bindings":[]}}')
            self.assertEqual(client.find_data_elements("C1"), [])
        self.assertEqual(
            [(call.args[0].full_url, call.args[1]) for call in transport.call_args_list],
            [("https://sparql.example/sparql", 17)],
        )

    def test_timeout_and_ten_megabyte_bound_apply_to_the_sparql_surface(self):
        client = SSISClient(Settings(timeout_seconds=17))
        client.sparql_http.sleep = lambda _: None
        with (
            patch("nci_si_mcp.http_client._open", side_effect=TimeoutError),
            self.assertRaises(UpstreamTimeoutError) as raised,
        ):
            client.find_data_elements("C1")
        self.assertEqual(raised.exception.details["seconds"], 17)
        self.assertEqual(raised.exception.details["attempts"], 3)
        response = FakeResponse(b"", {"Content-Length": str(10 * 1024 * 1024 + 1)})
        with (
            patch("nci_si_mcp.http_client._open", return_value=response),
            self.assertRaises(UpstreamTooLargeError) as raised,
        ):
            client.find_data_elements("C1")
        self.assertEqual(
            raised.exception.details,
            {"bound": "ssis_response_bytes", "limit": 10485760, "reached": 10485761},
        )


class SSISRetryContractsTest(ServerTestCase):
    def test_the_surface_retries_503_until_the_third_response(self):
        sparql = self.serve(Reply(503), Reply(503), sparql_reply([]))
        client = SSISClient(Settings(ssis_sparql_url=sparql.url))
        client.sparql_http.sleep = lambda _: None
        self.assertEqual(client.find_data_elements("C1"), [])
        self.assertEqual([path for path, _ in sparql.seen], ["/sparql"] * 3)
        self.assertEqual(sparql.bodies, [sparql.bodies[0]] * 3)
