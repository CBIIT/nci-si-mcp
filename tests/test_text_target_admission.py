"""Text admission uses the encoded request target before sending upstream content."""

import json
from dataclasses import replace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fakes import FakeSSIS, concept, release, terminology_row
from nci_si_mcp.cadsr import CaDSRClient
from nci_si_mcp.config import Settings
from nci_si_mcp.errors import InputValidationError
from nci_si_mcp.evs import EVSClient
from nci_si_mcp.validation import validate_text_target
from test_http_client import Reply, ServerTestCase
from test_server import ServerFixture


class GroundTextAdmissionTest(ServerFixture, ServerTestCase):
    def test_emoji_target_is_rejected_by_mcp_before_any_discovery(self):
        # Given legal code-point length but an oversized URL, no source is contacted.
        server = self.serve()
        self.context.evs = EVSClient(server.url)
        self.context.ssis = FakeSSIS(self.settings)
        with patch("nci_si_mcp.server.configure_logging"):
            failed, result = self.call("ground_value", text="😀" * 1000)
        self.assertTrue(failed)
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(result["error"]["details"]["parameter"], "text")
        self.assertEqual(server.seen, [])
        self.assertEqual(self.context.ssis.calls, [])

    def test_resolved_release_overflow_is_rejected_after_discovery_without_content(self):
        # The unresolved target is exactly 7,000; the discovered release adds six bytes.
        prefix = "/api/v1/concept/ncit_/search?term="
        suffix = "&type=contains&include=minimal%2Chighlights&fromRecord=0&pageSize=10"
        text = "😀" * 570 + "x" * (7000 - len(prefix + suffix) - 570 * 12)
        server = self.serve(Reply(body=json.dumps([terminology_row()]).encode()))
        self.context.evs = EVSClient(server.url)
        self.context.ssis = FakeSSIS(self.settings)
        with patch("nci_si_mcp.server.configure_logging"):
            failed, result = self.call("ground_value", text=text)
        self.assertTrue(failed)
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(result["error"]["details"]["parameter"], "text")
        self.assertEqual(len(server.seen), 1)
        self.assertEqual(urlsplit(server.seen[0][0]).path, "/api/v1/metadata/terminologies")
        self.assertEqual(self.context.ssis.calls, [])

    def test_admitted_ground_text_still_returns_its_concept(self):
        # An admissible text search still grounds its first result through MCP.
        self.context.ssis = FakeSSIS(self.settings)
        with (
            patch("nci_si_mcp.server.configure_logging"),
            patch.object(
                self.evs,
                "search_concepts",
                return_value=(1, [concept("C3262", "Neoplasm", active=True)]),
                create=True,
            ),
        ):
            failed, result = self.call("ground_value", text="Neoplasm")
        self.assertFalse(failed)
        self.assertEqual(result["concept"]["code"], "C3262")


class ClientTextAdmissionTest(ServerTestCase):
    def test_evs_checks_full_target_including_base_path_release_offset_and_status(self):
        # A later filtered page can exceed the ceiling although its first page fits.
        server = self.serve(Reply(body=b'{"total":0}'))
        client = EVSClient(server.url + "/gateway")
        selected = release("release-" + "r" * 50)
        query = "😀" * 568
        self.assertEqual(client.search_concepts(selected, query, "lexical", 0, 10), (0, []))
        with self.assertRaises(InputValidationError) as raised:
            client.search_concepts(selected, query, "lexical", 123456789, 10, "Retired_Concept")
        self.assertEqual(raised.exception.details["parameter"], "query")
        self.assertEqual(len(server.seen), 1)
        target = urlsplit(server.seen[0][0])
        self.assertEqual(
            target.path, "/gateway/api/v1/concept/ncit_" + selected.version + "/search"
        )
        self.assertEqual(parse_qs(target.query)["term"], [query])

    def test_cadsr_text_routes_check_encoded_target_with_registry_pin(self):
        # Each owning route rejects excess before HTTP and preserves admitted text verbatim.
        for operation, parameter, wire_name in (
            ("get_by_question_text", "questionText", "documentText"),
            ("search_data_elements", "query", "keyword"),
        ):
            with self.subTest(operation=operation):
                pin = "r" * 100
                server = self.serve(
                    Reply(body=json.dumps({"DataElements": [], "registryRelease": pin}).encode())
                )
                client = CaDSRClient(replace(Settings(), cadsr_base_url=server.url + "/gateway"))
                method = getattr(client, operation)
                args = (10,) if operation == "search_data_elements" else ()
                with self.assertRaises(InputValidationError) as raised:
                    method("😀" * 1000, *args, registry_release=pin)
                self.assertEqual(raised.exception.details["parameter"], parameter)
                self.assertEqual(server.seen, [])
                response = method("a + 😀", *args, registry_release=pin)
                self.assertEqual(
                    response, [] if not args else {"DataElements": [], "registryRelease": pin}
                )
                self.assertEqual(len(server.seen), 1)
                params = parse_qs(urlsplit(server.seen[0][0]).query)
                self.assertEqual(params[wire_name], ["a + 😀"])
                self.assertEqual(params["registryRelease"], [pin])

    def test_validator_admits_exactly_7000_ascii_target_characters(self):
        # The authority does not consume request-target bytes; the path and query do.
        prefix = "/path?text="
        target = prefix + "x" * (7000 - len(prefix))
        self.assertIsNone(validate_text_target("https://example.test" + target, "text"))
        with self.assertRaises(InputValidationError) as raised:
            validate_text_target("https://example.test" + target + "x", "text")
        self.assertEqual(
            raised.exception.details,
            {
                "parameter": "text",
                "reason": "Encoded request target is 7001 bytes; use at most 7000 bytes",
            },
        )
