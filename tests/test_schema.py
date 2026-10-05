import json
import re
from copy import deepcopy
from dataclasses import replace
from itertools import product
from typing import get_args

from jsonschema import Draft202012Validator

from fakes import release
from nci_si_mcp.config import BASE_URL_VARIABLES
from nci_si_mcp.errors import ErrorCode, PlatformError, serialise
from nci_si_mcp.http_client import UpstreamUnavailableError
from nci_si_mcp.registry import ToolSpec, invoke
from nci_si_mcp.validation import PROFILES, RELEASE_CHANNELS, UPSTREAM_MODES
from test_server import ServerFixture


class SchemaTest(ServerFixture):
    def validators(self):
        listing = self.session(lambda client: client.list_tools())
        return {tool.name: Draft202012Validator(tool.output_schema) for tool in listing.tools}

    def result(self, name, **arguments):
        result = self.session(lambda client: client.call_tool(name, arguments))
        self.assertEqual(result.structured_content, json.loads(result.content[0].text))
        return result.structured_content

    def test_every_schema_is_valid_and_rejects_malformed_errors(self):
        validators = self.validators()
        self.assertEqual(len(validators), 5)
        for name, validator in validators.items():
            with self.subTest(tool=name):
                validator.check_schema(validator.schema)
                self.assertFalse(validator.is_valid({}))
                self.assertFalse(validator.is_valid({"error": {"message": "missing code"}}))
                self.assertFalse(
                    validator.is_valid(
                        {"error": {"code": "unknown", "message": "retry", "correlationId": "call"}}
                    )
                )
                for code in get_args(ErrorCode):
                    validator.validate(serialise(PlatformError(code, "Retry", reason="test")))

    def test_current_success_shapes_validate_without_rewriting_fields(self):
        invoke(self.context, "index_codes", ["C3262", "C4741"])
        calls = {
            "ncit_lookup": {"code": "C3262"},
            "ncit_search": {"query": "Neoplasm", "limit": 1},
            "ncit_traverse": {"start_codes": ["C3262"], "max_nodes": 1, "max_depth": 1},
            "ncit_release_info": {},
            "cadsr_status": {},
        }
        validators = self.validators()
        for name, arguments in calls.items():
            with self.subTest(tool=name):
                data = self.result(name, **arguments)
                validators[name].validate(data)
                self.assertNotIn("result", data)
                broken = dict(data)
                broken.pop(next(iter(data)))
                self.assertFalse(validators[name].is_valid(broken))

    def test_lookup_schema_checks_nested_types_and_preserves_upstream_fields(self):
        self.evs.concepts["C3262"]["definitions"] = [{"definition": "A definition"}]
        data = self.result("ncit_lookup", code="C3262")
        validator = self.validators()["ncit_lookup"]
        validator.validate(data)
        self.assertEqual(data["provenance"]["upstream"]["version"], "26.06e")
        self.assertIn("sourceUri", data["provenance"])
        broken = deepcopy(data)
        broken["provenance"]["release"]["identifier"] = 42
        self.assertFalse(validator.is_valid(broken))
        broken = deepcopy(data)
        broken["code"] = 42
        self.assertFalse(validator.is_valid(broken))

    def test_empty_search_and_cached_lookup_optional_fields(self):
        invoke(self.context, "index_codes", ["C3262"])
        validators = self.validators()
        empty = self.result("ncit_search", query="zzzzzz", mode="bm25")
        validators["ncit_search"].validate(empty)
        self.assertEqual(empty["hits"], [])
        self.assertIn("provenance", empty)
        self.evs.errors["get_terminologies"] = UpstreamUnavailableError("offline")
        cached = self.result("ncit_lookup", code="C3262")
        validators["ncit_lookup"].validate(cached)
        self.assertEqual(cached["fallback"]["reason"], "upstream_unavailable")

    def test_discovery_nested_errors_and_absent_provenance_validate(self):
        self.evs.errors["get_terminologies"] = UpstreamUnavailableError("offline")
        self.evs.errors["get_api_version"] = UpstreamUnavailableError("offline")
        data = self.result("ncit_release_info")
        validator = self.validators()["ncit_release_info"]
        validator.validate(data)
        self.assertNotIn("provenance", data)
        self.assertIsNone(data["active_index"])
        self.assertEqual(data["selected_monthly_release"]["error"]["code"], "upstream_unavailable")
        broken = deepcopy(data)
        broken["selected_monthly_release"]["error"]["code"] = "unknown"
        self.assertFalse(validator.is_valid(broken))

    def test_recursive_kind_truncation_and_node_types_are_checked(self):
        data = self.result("ncit_traverse", start_codes=["C3262"], max_nodes=1)
        validator = self.validators()["ncit_traverse"]
        validator.validate(data)
        self.assertTrue(data["truncation"]["occurred"])
        self.assertIn("perKind", data["truncation"])
        broken = deepcopy(data)
        first = next(iter(broken["truncation"]["perKind"].values()))
        first["omitted"] = "not a count"
        self.assertFalse(validator.is_valid(broken))
        broken = deepcopy(data)
        broken["nodes"][0]["provenance"]["depth"] = "zero"
        self.assertFalse(validator.is_valid(broken))

    def test_real_error_results_validate(self):
        calls = {
            "ncit_lookup": {"code": "bad"},
            "ncit_search": {"query": ""},
            "ncit_traverse": {"start_codes": []},
        }
        validators = self.validators()
        for name, arguments in calls.items():
            with self.subTest(tool=name):
                data = self.result(name, **arguments)
                validators[name].validate(data)
                self.assertEqual(data["error"]["code"], "invalid_request")

    def test_error_record_requires_all_shared_fields(self):
        valid = serialise(PlatformError("not_found", "Use an existing code"))
        for name, validator in self.validators().items():
            validator.validate(valid)
            for field in ("code", "message", "correlationId"):
                with self.subTest(tool=name, field=field):
                    broken = deepcopy(valid)
                    del broken["error"][field]
                    self.assertFalse(validator.is_valid(broken))

    def test_occurred_truncation_requires_its_bound_and_counts(self):
        data = self.result("ncit_traverse", start_codes=["C3262"], max_nodes=1)
        validator = self.validators()["ncit_traverse"]
        self.assertTrue(data["truncation"]["occurred"])
        for field in ("bound", "limit", "reached", "omitted", "exact"):
            with self.subTest(field=field):
                broken = deepcopy(data)
                del broken["truncation"][field]
                self.assertFalse(validator.is_valid(broken))

    def listing_bytes(self):
        listing = self.session(lambda client: client.list_tools())
        return json.dumps(
            [tool.model_dump(by_alias=True) for tool in listing.tools], sort_keys=True
        )

    def test_schema_surface_is_static_in_every_configuration(self):
        before = {}
        for profile in PROFILES:
            self.settings = replace(self.settings, profile=profile)
            before[profile] = self.listing_bytes()
        urls = dict.fromkeys(BASE_URL_VARIABLES, "https://fixture.test")
        for profile, channel, mode in product(PROFILES, RELEASE_CHANNELS, UPSTREAM_MODES):
            with self.subTest(profile=profile, channel=channel, mode=mode):
                self.settings = replace(
                    self.settings,
                    profile=profile,
                    release_channel=channel,
                    upstream_mode=mode,
                    **urls,
                )
                self.evs.release = release("99.01a", channel=channel)
                self.assertEqual(self.listing_bytes(), before[profile])

    def test_schema_surface_is_static_after_calls_and_upstream_failure(self):
        before = self.listing_bytes()
        self.result("ncit_lookup", code="C3262")
        self.assertEqual(self.listing_bytes(), before)
        self.evs.errors["get_terminologies"] = UpstreamUnavailableError("offline")
        self.result("ncit_lookup", code="C3262")
        self.assertEqual(self.listing_bytes(), before)

    def test_descriptions_have_no_unfinished_text_or_unsupported_search_values(self):
        listing = self.session(lambda client: client.list_tools())
        text = json.dumps([tool.model_dump(by_alias=True) for tool in listing.tools])
        self.assertIsNone(re.search(r"\b(TODO|FIXME|XXX|TBD|HACK|placeholder|debug)\b", text, re.I))
        search = next(tool for tool in listing.tools if tool.name == "ncit_search")
        modes = search.input_schema["properties"]["mode"]["enum"]
        self.assertEqual(set(modes), {"bm25", "vector", "hybrid"})
        for unsupported in ("lucene", "regex", "contains"):
            with self.subTest(mode=unsupported):
                self.assertNotIn(f"`{unsupported}`", search.description)
                response = self.session(
                    lambda client, mode=unsupported: client.call_tool(
                        "ncit_search", {"query": "term", "mode": mode}
                    )
                )
                self.assertTrue(response.is_error)

    def test_missing_output_declaration_fails_at_registration(self):
        def undeclared(context):
            return {"anything": True}

        with self.assertRaisesRegex(TypeError, "output"):
            ToolSpec(handler=undeclared, group="evs", resolution=False)
