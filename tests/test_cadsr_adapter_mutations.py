"""The actual adapters preserve argument paths, injection and command names."""

import io
import json
import logging
import sys
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from nci_si_mcp.cli import build_parser, main
from nci_si_mcp.context import Context
from nci_si_mcp.registry import invoke
from nci_si_mcp.release import registry_state
from test_server import ServerFixture


class CaDSRAdapterMutationTest(ServerFixture):
    def injected_context(self):
        self.state = registry_state(
            "2026-07-01T22:19", None, source_distribution="releasedCDEsXML-OD.zip"
        )
        client = SimpleNamespace(resolve_registry_release=lambda: self.state)
        return Context(
            self.settings,
            cadsr=client,
            evs=self.evs,
            index=self.context.index,
            embedding_provider=self.context.embedding_provider,
        )

    def test_injected_cadsr_produces_the_result_without_a_release_selection_audit(self):
        context = self.injected_context()
        logging.disable(logging.NOTSET)
        with self.assertLogs("nci_si_mcp.audit", level="INFO") as captured:
            result = invoke(context, "resolve_registry_release")
        self.assertEqual(result, self.state.to_dict())
        self.assertNotIn("selection", captured.records[-1].structured.get("release", {}))

    def test_registry_resolution_cli_command_returns_the_injected_content_state(self):
        self.assertIn("resolve-registry-release", build_parser().format_help())
        context = self.injected_context()
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["nci-si-mcp", "resolve-registry-release"]),
            patch("nci_si_mcp.cli.Context", return_value=context),
            patch("nci_si_mcp.cli.configure_logging"),
            redirect_stdout(output),
        ):
            status = main()
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue()), self.state.to_dict())

    def test_real_mcp_names_missing_top_level_and_malformed_nested_arguments(self):
        async def interact(client):
            results = []
            for operation, arguments in (
                ("get_concept", {"terminology": "ncit"}),
                ("match_data_elements", {"entities": [{}]}),
                ("match_data_elements", {"entities": [{"name": 5}]}),
            ):
                result = await client.call_tool(operation, arguments)
                results.append(result.structured_content["error"])
            return results

        errors = self.session(interact)
        self.assertEqual([error["code"] for error in errors], ["invalid_request"] * 3)
        self.assertEqual(
            [error.get("details", {}).get("parameter") for error in errors],
            ["code", "entities.0", "entities"],
        )
        self.assertEqual(self.evs.calls, [])
