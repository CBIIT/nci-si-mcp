import asyncio
import hashlib
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import get_args
from unittest.mock import patch

import yaml
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter

from nci_si_mcp import cli, results, validation
from nci_si_mcp.errors import IndexCompatibilityError
from nci_si_mcp.http_client import UpstreamRejectedError, UpstreamUnavailableError
from nci_si_mcp.registry import OPERATIONS, SPECS, invoke
from nci_si_mcp.server import _audit_tools
from test_audit import captured, records
from test_server import ServerFixture


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return {"sha256": hashlib.sha256(encoded.encode()).hexdigest()}


def validator(operation):
    return Draft202012Validator(TypeAdapter(OPERATIONS[operation].output).json_schema())


class RegistryMutationTest(ServerFixture):
    def test_search_cursor_is_hashed_even_when_the_option_is_refused(self):
        cursor = "private-cursor-canary"
        with captured() as stream:
            result = invoke(
                self.context, "search_concepts", "ncit", "26.06e", "Neoplasm", cursor=cursor
            )
        self.assertEqual(result["error"]["code"], "invalid_request")
        (record,) = records(stream)
        self.assertEqual(record["parameters"]["cursor"], digest(cursor))
        self.assertNotIn(cursor, stream.getvalue())

    def test_legacy_relationship_filter_is_hashed_in_completion_record(self):
        names = ["Disease_Has_Abnormal_Cell"]
        with captured() as stream:
            result = invoke(
                self.context, "traverse", ["C3262"], max_depth=1, relationship_names=names
            )
        self.assertEqual([edge["target_code"] for edge in result["edges"]], ["C4741", "C12922"])
        (record,) = records(stream)
        self.assertEqual(record["parameters"]["relationship_names"], digest(names))
        self.assertNotIn(names[0], stream.getvalue())

    def test_incompatible_index_error_names_the_rebuild_next_step(self):
        with patch.object(
            self.context.index, "search_snapshot", side_effect=IndexCompatibilityError("bad")
        ):
            result = invoke(self.context, "search", "Neoplasm")
        self.assertEqual(result["error"]["code"], "internal_error")
        self.assertTrue(
            result["error"]["message"].endswith(
                "Use the original embedding settings, or run `index-rebuild` and activate its "
                "evaluated build with `index-activate`."
            )
        )

    def test_rejected_upstream_error_names_the_base_url_next_step(self):
        self.evs.errors["get_concept"] = UpstreamRejectedError("rejected", status=403)
        result = invoke(self.context, "lookup", "C3262", live_only=True)
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.assertTrue(
            result["error"]["message"].endswith(
                "Check the configured base URL and credentials for this upstream."
            )
        )

    def test_invisible_tool_names_and_arguments_are_hashed(self):
        async def rejected(_):
            return {"isError": True}

        request = SimpleNamespace(
            method="tools/call",
            params={"name": "get_concept", "arguments": {"code": "C3262"}},
            meta={"correlationId": "hidden-tool"},
        )
        with captured() as stream:
            result = asyncio.run(_audit_tools(self.context, "cadsr")(request, rejected))
        self.assertEqual(result, {"isError": True})
        (record,) = records(stream)
        self.assertEqual(record["tool"], json.dumps(digest("get_concept"), separators=(",", ":")))
        self.assertEqual(list(record["parameters"].values()), [digest("C3262")])
        self.assertEqual(record["correlationId"], "hidden-tool")

    def test_invalid_environment_error_has_an_actionable_next_step(self):
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"NCI_SI_TIMEOUT_SECONDS": "0"}, clear=True),
            patch.object(sys, "argv", ["nci-si-mcp", "release-info"]),
            redirect_stdout(output),
        ):
            status = cli.main()
        result = json.loads(output.getvalue())
        self.assertEqual(status, 1)
        self.assertEqual(result["error"]["details"]["parameter"], "NCI_SI_TIMEOUT_SECONDS")
        self.assertTrue(
            result["error"]["message"].endswith("Fix the environment variable and rerun.")
        )

    def test_cli_requires_a_subcommand(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            cli.build_parser().parse_args([])
        self.assertEqual(raised.exception.code, 2)

    def test_cli_help_describes_each_registered_command(self):
        help_text = " ".join(cli.build_parser().format_help().split())
        for spec in SPECS:
            if spec.command:
                with self.subTest(command=spec.command):
                    self.assertIn(" ".join(spec.description.splitlines()[0].split()), help_text)

    def test_provenance_correlation_is_required_in_output_schema(self):
        result = invoke(self.context, "lookup", "C3262")
        schema = validator("lookup")
        self.assertTrue(schema.is_valid(result))
        del result["provenance"]["correlationId"]
        self.assertFalse(schema.is_valid(result))

    def test_occurred_truncation_cannot_claim_false_with_counts(self):
        result = invoke(self.context, "traverse", ["C3262"], max_nodes=1)
        record = result["truncation"]
        schema = Draft202012Validator(TypeAdapter(results.Truncated).json_schema())
        self.assertTrue(schema.is_valid(record))
        record["occurred"] = False
        self.assertFalse(schema.is_valid(record))

    def test_fallback_reason_is_a_closed_value_in_lookup_schema(self):
        invoke(self.context, "index_codes", ["C3262"])
        self.evs.errors["get_concept"] = UpstreamUnavailableError("offline")
        result = invoke(self.context, "lookup", "C3262")
        schema = validator("lookup")
        self.assertTrue(schema.is_valid(result))
        self.assertEqual(result["fallback"]["reason"], "upstream_unavailable")
        result["fallback"]["reason"] = "invented"
        self.assertFalse(schema.is_valid(result))

    def test_release_report_requires_active_index_even_when_null(self):
        result = invoke(self.context, "release_info")
        schema = validator("release_info")
        self.assertTrue(schema.is_valid(result))
        self.assertIsNone(result.pop("active_index"))
        self.assertFalse(schema.is_valid(result))

    def test_search_schema_rejects_unknown_legacy_mode(self):
        invoke(self.context, "index_codes", ["C3262"])
        result = invoke(self.context, "search", "Neoplasm")
        schema = validator("search")
        self.assertTrue(schema.is_valid(result))
        result["mode"] = "invented"
        self.assertFalse(schema.is_valid(result))

    def test_traversal_schema_rejects_unknown_edge_type(self):
        result = invoke(self.context, "traverse", ["C3262"], max_depth=1)
        schema = validator("traverse")
        self.assertTrue(schema.is_valid(result))
        self.assertTrue(result["edges"])
        result["edges"][0]["edge_type"] = "invented"
        self.assertFalse(schema.is_valid(result))

    def test_release_report_accepts_selected_release_error_arm(self):
        self.evs.errors["get_terminologies"] = UpstreamUnavailableError("offline")
        result = invoke(self.context, "release_info")
        self.assertEqual(result["selected_release"]["error"]["code"], "upstream_unavailable")
        self.assertNotIn("error", result)
        self.assertTrue(validator("release_info").is_valid(result))

    def test_traversal_schema_rejects_unknown_assertion_direction(self):
        result = invoke(self.context, "traverse", ["C3262"], max_depth=1)
        schema = validator("traverse")
        self.assertTrue(schema.is_valid(result))
        result["edges"][0]["provenance"]["direction"] = "sideways"
        self.assertFalse(schema.is_valid(result))

    def test_closed_output_values_equal_the_specification(self):
        path = Path(__file__).resolve().parents[1] / "spec" / "records.yaml"
        spec = yaml.safe_load(path.read_text())
        cases = (
            (validation.ProvenanceSource, "provenance", "source"),
            (validation.ServedBy, "provenance", "servedBy"),
            (validation.TruncationBound, "truncation", "bound"),
        )
        for declaration, record, field in cases:
            with self.subTest(record=record, field=field):
                self.assertEqual(
                    set(get_args(declaration)), set(spec[record]["fields"][field]["values"])
                )
