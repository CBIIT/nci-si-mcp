import json
from dataclasses import replace
from typing import Literal, TypedDict
from unittest.mock import patch

from nci_si_mcp import cli
from nci_si_mcp.caching import cache_call, select_cache_hint
from nci_si_mcp.http_client import (
    UpstreamRejectedError,
    UpstreamTimeoutError,
    UpstreamTooLargeError,
    UpstreamUnavailableError,
)
from nci_si_mcp.registry import OPERATIONS, ToolSpec, invoke
from test_server import ServerFixture


class Echo(TypedDict):
    code: str
    limit: int
    mode: str


class RegistryTest(ServerFixture):
    def test_profiles_select_only_the_current_inventory_and_keep_group_metadata(self):
        evs = {
            "get_concept",
            "get_concepts",
            "list_relationships",
            "resolve_retired_code",
            "get_concept_subsets",
            "expand_value_set",
            "get_concept_mappings",
            "search_concepts",
            "get_concept_hierarchy",
            "get_concept_neighborhood",
            "resolve_release",
            "list_terminologies",
        }
        cadsr = {
            "get_data_element",
            "search_data_elements",
            "list_contexts",
            "list_classification_schemes",
            "resolve_registry_release",
            "get_form",
            "get_permissible_value",
            "get_code_map",
            "match_data_elements",
            "match_value_meanings",
        }
        seam = {
            "find_data_elements_for_concept",
            "get_concept_for_permissible_value",
            "resolve_stored_value",
            "get_release_alignment",
        }
        workflows = {"ground_value", "expand_cohort", "harmonize_data_dictionary"}
        expected = {"evs": evs, "cadsr": cadsr, "unified": evs | cadsr | seam | workflows}
        for profile, names in expected.items():
            with self.subTest(profile=profile):
                self.settings = replace(self.settings, profile=profile)
                tools = self.session(lambda client: client.list_tools()).tools
                self.assertEqual({tool.name for tool in tools}, names)
                for tool in tools:
                    groups = {
                        **dict.fromkeys(evs, "evs"),
                        **dict.fromkeys(cadsr, "cadsr"),
                        **dict.fromkeys(seam, "cross-domain"),
                        **dict.fromkeys(workflows, "workflow"),
                    }
                    self.assertEqual(tool.meta["group"], groups[tool.name])

    def test_every_tool_advertises_all_four_read_only_annotations(self):
        tools = self.session(lambda client: client.list_tools()).tools
        expected = {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
        for tool in tools:
            with self.subTest(tool=tool.name):
                actual = tool.annotations.model_dump(by_alias=True)
                self.assertEqual({key: actual[key] for key in expected}, expected)

    def test_a_single_new_declaration_drives_both_adapters(self):
        def echo(context, code: str, limit: int = 3, mode: Literal["a", "b"] = "a"):
            """Echo the shared arguments."""
            return {"code": code, "limit": limit, "mode": mode}

        spec = ToolSpec(echo, "evs", Echo, False, name="echo", command="echo")
        with (
            patch("nci_si_mcp.server.SPECS", (spec,)),
            patch("nci_si_mcp.cli.SPECS", (spec,)),
            patch.dict(OPERATIONS, {spec.operation: spec}),
        ):
            parser = cli.build_parser()
            default = cli._run(self.context, parser.parse_args(["echo", "C1"]))
            changed = cli._run(
                self.context, parser.parse_args(["echo", "C1", "--limit", "7", "--mode", "b"])
            )
            result = self.session(
                lambda client: client.call_tool("echo", {"code": "C1", "limit": 7, "mode": "b"})
            )
        self.assertEqual(default, {"code": "C1", "limit": 3, "mode": "a"})
        self.assertEqual(changed, {"code": "C1", "limit": 7, "mode": "b"})
        self.assertEqual(result.structured_content, changed)
        self.assertEqual(json.loads(result.content[0].text), changed)

    def test_cli_maintenance_commands_are_available_in_every_profile(self):
        for profile in ("evs", "cadsr", "unified"):
            with self.subTest(profile=profile):
                self.context.settings = replace(self.settings, profile=profile)
                args = cli.build_parser().parse_args(["index-sample", "C3262"])
                result = cli._run(self.context, args)
                self.assertEqual(result["concepts"], 1)
                self.assertEqual(cli.build_parser().parse_args(["evaluate"]).operation, "evaluate")

    def test_handler_owned_cache_policy_has_no_registry_default(self):
        def content(context):
            before = dict(hint)
            select_cache_hint(resolution=False, unpinned=True)
            return {"before": before}

        spec = ToolSpec(content, "cross-domain", dict)
        with cache_call() as hint, patch.dict(OPERATIONS, {spec.operation: spec}):
            result = invoke(self.context, spec.operation)
        self.assertEqual(result, {"before": {}})
        self.assertEqual(hint, {"ttlMs": 3_600_000, "cacheScope": "public"})

    def test_upstream_details_survive_the_boundary_without_an_evs_bridge(self):
        failures = {
            UpstreamUnavailableError: "upstream_unavailable",
            UpstreamRejectedError: "upstream_unavailable",
            UpstreamTimeoutError: "timeout",
            UpstreamTooLargeError: "bound_exceeded",
        }
        details = {"surface": "EVS", "status": 503, "attempts": 3}
        for kind, code in failures.items():
            with self.subTest(kind=kind.__name__):
                self.evs.errors["get_concept"] = kind("failed", **details)
                result = invoke(
                    self.context, "lookup", "C3262", live_only=True, _correlation_id="registry-call"
                )
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(result["error"]["details"], details)
                self.assertEqual(result["error"]["correlationId"], "registry-call")
