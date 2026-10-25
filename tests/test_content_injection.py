"""Hostile upstream failure bodies cannot become MCP error wording."""

import logging
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures
from nci_si_mcp.context import Context
from test_server import ServerFixture


class ContentInjectionErrorTest(ServerFixture):
    def setUp(self):
        super().setUp()
        logging.disable(logging.NOTSET)

    def test_masked_failure_messages_are_server_owned(self):
        # Given instruction text in real error-envelope fields, when MCP reads a CDE,
        # then the exact error record and diagnostics contain only server-owned wording.
        fixtures = Path(__file__).parents[1] / "acceptance/fixtures"
        with FixtureServer(load_fixtures(fixtures)) as upstream:
            self.settings = replace(self.settings, cadsr_base_url=upstream.base_url("cadsr"))
            self.context = Context(
                self.settings, embedding_provider=self.context.embedding_provider
            )
            for kind, description in (
                ("webmethods", "a webMethods error envelope"),
                ("fhir", "a FHIR OperationOutcome that reports an error"),
            ):
                with (
                    self.subTest(kind=kind),
                    patch("nci_si_mcp.server.configure_logging"),
                    self.assertLogs("nci_si_mcp", level="DEBUG") as logs,
                ):
                    upstream.activate(f"injection/{kind}-error")
                    result = self.session(
                        lambda client: client.call_tool("get_data_element", {"publicId": "2200604"})
                    )
                    self.assertTrue(result.is_error)
                    error = result.structured_content["error"]
                    self.assertEqual(
                        result.structured_content,
                        {
                            "error": {
                                "code": "upstream_unavailable",
                                "message": "CADSR /NCIAPI/1.0/api/DataElement/2200604 "
                                f"answered with {description} in a success response. "
                                "Retry later; if it persists, "
                                "check that the platform's base URL is the right one.",
                                "details": {"surface": "cadsr", "attempts": 1},
                                "correlationId": error["correlationId"],
                            }
                        },
                    )
                    self.assertTrue(error["correlationId"])
                    self.assertNotIn("X29-", str(result))
                    self.assertNotIn("X29-", "".join(logs.output))

    def test_hostile_content_on_http_failure_is_not_used_as_error_text(self):
        fixtures = Path(__file__).parents[1] / "acceptance/fixtures"
        cases = {
            "evs": ("get_concept", {"terminology": "ncit", "code": "C4817", "release": "26.09d"}),
            "cadsr": ("get_data_element", {"publicId": "2200604"}),
            "cross-domain": (
                "find_data_elements_for_concept",
                {"conceptCode": "C17357", "release": "26.09d"},
            ),
            "workflow": ("ground_value", {"conceptCode": "C4817", "release": "26.09d"}),
        }
        requests = {
            "evs": ("EVS", "/api/v1/concept/ncit_26.09d/C4817", "evs"),
            "cadsr": ("CADSR", "/NCIAPI/1.0/api/DataElement/2200604", "cadsr"),
            "cross-domain": ("SSIS_SPARQL", "/sparql", "ssis_sparql"),
            "workflow": ("SSIS_SPARQL", "/sparql", "ssis_sparql"),
        }
        with FixtureServer(load_fixtures(fixtures)) as upstream:
            self.settings = replace(
                self.settings,
                evs_base_url=upstream.base_url("evs"),
                cadsr_base_url=upstream.base_url("cadsr"),
                cadsr_ftp_url=upstream.base_url("cadsr-ftp"),
                ssis_sparql_url=upstream.base_url("ssis-sparql"),
            )
            self.context = Context(
                self.settings, embedding_provider=self.context.embedding_provider
            )
            for group, (tool, arguments) in cases.items():
                with (
                    self.subTest(group=group),
                    patch("nci_si_mcp.server.configure_logging"),
                    self.assertLogs("nci_si_mcp", level="DEBUG") as logs,
                ):
                    upstream.activate(f"injection/{group}-error")
                    result = self.session(
                        lambda client, t=tool, a=arguments: client.call_tool(t, a)
                    )
                    self.assertTrue(result.is_error)
                    error = result.structured_content["error"]
                    self.assertEqual(error["code"], "upstream_unavailable")
                    label, path, surface = requests[group]
                    self.assertEqual(
                        error["message"],
                        f"{label} request failed for {path}: HTTP 400 Bad Request. "
                        "Check the configured base URL and credentials for this upstream.",
                    )
                    self.assertEqual(
                        error["details"], {"surface": surface, "status": 400, "attempts": 1}
                    )
                    self.assertEqual(set(error), {"code", "message", "details", "correlationId"})
                    self.assertTrue(error["correlationId"])
                    self.assertNotIn("X29-", str(result))
                    self.assertNotIn("X29-", "".join(logs.output))
                    self.assertEqual(set(result.structured_content), {"error"})
