"""Regression contracts for cross-domain query paging and exact value resolution."""

import base64
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fakes import FakeEVS, FakeSSIS, concept
from nci_si_mcp.caching import cache_call
from nci_si_mcp.config import Settings
from nci_si_mcp.context import Context
from nci_si_mcp.registry import invoke
from nci_si_mcp.ssis import CADSR_GRAPH, NCIT_GRAPH, SSISClient
from test_seam import NCIT, element, value_row


def bindings(rows):
    return {
        "results": {
            "bindings": [
                {key: {"type": "literal", "value": value} for key, value in row.items()}
                for row in rows
            ]
        }
    }


class SeamQueryMutationTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = Settings(data_dir=Path(directory.name))
        self.ssis = FakeSSIS(settings)
        self.evs = FakeEVS([concept("C1", active=True), concept("C2", active=True)])
        self.context = Context(settings, evs=self.evs, ssis=self.ssis)
        blocker = patch(
            "nci_si_mcp.http_client._open", side_effect=AssertionError("Unexpected HTTP")
        )
        blocker.start()
        self.addCleanup(blocker.stop)

    def call(self, operation="find_data_elements_for_concept", **arguments):
        with cache_call() as hint:
            result = invoke(self.context, operation, _correlation_id="seam-query", **arguments)
        self.hint = hint
        return result

    def resolve(self, **arguments):
        return self.call(
            "get_concept_for_permissible_value",
            **({"dataElementId": "123", "value": "Male", "release": "26.06e"} | arguments),
        )

    def test_descendant_option_reaches_both_queries_and_binds_the_cursor(self):
        client = SSISClient(self.context.settings)
        self.context.ssis = client
        rows = [element(123), element(456)]
        values = [{"id": "123", "version": "1", "value": "Male", "concept": NCIT + "C1"}]
        arguments = {
            "conceptCode": "C1",
            "expandDescendants": True,
            "includePermissibleValues": True,
        }
        with (
            patch.object(client, "get_graph_identities", return_value=self.ssis.graphs),
            patch.object(
                client.sparql_http, "post_form", side_effect=[bindings(rows), bindings(values)]
            ) as post,
        ):
            first = self.call(**arguments, limit=1)
        self.assertEqual(first["dataElements"][0]["dataElement"]["publicId"], "123")
        self.assertEqual(first["permissibleValues"], [])
        for request in post.call_args_list:
            self.assertIn("rdfs:subClassOf* ncit:C1", request.args[1]["query"])
        changed = self.call(
            **(arguments | {"expandDescendants": False}), limit=1, cursor=first["nextCursor"]
        )
        self.assertEqual(changed["error"]["code"], "invalid_request")
        self.assertEqual(changed["error"]["details"]["parameter"], "cursor")

    def test_default_value_flag_skips_the_query_and_exact_cap_is_complete(self):
        self.ssis.elements = [element(i) for i in range(1, 1001)]
        self.ssis.values = [{"id": "1", "version": "1", "value": "M", "concept": NCIT + "C1"}]
        result = self.call(conceptCode="C1", limit=1000)
        self.assertEqual(len(result["dataElements"]), 1000)
        self.assertEqual(result["truncation"], {"occurred": False})
        self.assertNotIn("permissibleValues", result)
        self.assertNotIn("nextCursor", result)
        self.assertEqual(self.ssis.calls, [("identities",), ("elements", "C1", False, 1000)])

    def test_exact_shared_cap_including_values_is_complete(self):
        self.ssis.elements = [element(i) for i in range(1, 1000)]
        self.ssis.values = [{"id": "1", "version": "1", "value": "M", "concept": NCIT + "C1"}]
        result = self.call(conceptCode="C1", includePermissibleValues=True, limit=1000)
        self.assertEqual(len(result["dataElements"]), 999)
        self.assertEqual(result["permissibleValues"][0]["value"], "M")
        self.assertEqual(result["permissibleValues"][0]["conceptTerminology"], "ncit")
        self.assertEqual(result["truncation"], {"occurred": False})
        self.assertNotIn("nextCursor", result)

    def test_default_page_limit_is_one_hundred(self):
        self.ssis.elements = [element(i) for i in range(1, 102)]
        first = self.call(conceptCode="C1")
        second = self.call(conceptCode="C1", cursor=first["nextCursor"])
        self.assertEqual(len(first["dataElements"]), 100)
        self.assertEqual(
            [row["dataElement"]["publicId"] for row in second["dataElements"]], ["101"]
        )
        self.assertEqual(second["truncation"], {"occurred": False})
        self.assertNotIn("nextCursor", second)

    def test_above_maximum_page_limit_clamps_as_required_by_spec(self):
        self.ssis.elements = [element(i) for i in range(1, 1002)]
        result = self.call(conceptCode="C1", limit=1001)
        self.assertEqual(len(result["dataElements"]), 1000)
        self.assertEqual(result["dataElements"][-1]["dataElement"]["publicId"], "1000")
        self.assertEqual(result["truncation"]["limit"], 1000)
        self.assertEqual(result["truncation"]["omitted"], 1)
        self.assertNotIn("nextCursor", result)

    def test_last_page_cannot_leak_the_sentinel_when_its_limit_overshoots(self):
        self.ssis.elements = [element(i) for i in range(1, 1002)]
        first = self.call(conceptCode="C1", limit=200)
        # A valid continuation position can be resumed without being a page-size multiple.
        token = json.loads(base64.urlsafe_b64decode(first["nextCursor"]))
        token["offset"] = 900
        cursor = base64.urlsafe_b64encode(json.dumps(token).encode()).decode()
        final = self.call(conceptCode="C1", limit=200, cursor=cursor)
        self.assertEqual(
            [row["dataElement"]["publicId"] for row in final["dataElements"]],
            [str(i) for i in range(901, 1001)],
        )
        self.assertEqual(final["truncation"]["omitted"], 1)
        self.assertNotIn("nextCursor", final)

    def test_continuations_compare_an_above_maximum_limit_as_applied(self):
        self.ssis.elements = [element(i) for i in range(1, 1002)]
        first = self.call(conceptCode="C1", limit=200)
        token = json.loads(base64.urlsafe_b64decode(first["nextCursor"]))
        # A maximum-sized first page fills the total cap and issues no cursor.
        # Construct a valid continuation to exercise applied-limit comparison itself.
        token["arguments"]["limit"] = 1000
        token["offset"] = 900
        cursor = base64.urlsafe_b64encode(json.dumps(token).encode()).decode()
        for limit in (1000, 1001):
            with self.subTest(limit=limit):
                result = self.call(conceptCode="C1", limit=limit, cursor=cursor)
                self.assertEqual(
                    [row["dataElement"]["publicId"] for row in result["dataElements"]],
                    [str(i) for i in range(901, 1001)],
                )
                self.assertEqual(result["truncation"]["limit"], 1000)
                self.assertNotIn("nextCursor", result)

    def test_changed_value_rows_expire_a_cursor_even_when_elements_are_unchanged(self):
        self.ssis.elements = [element(123)]
        self.ssis.values = [{"id": "123", "version": "1", "value": "Male", "concept": NCIT + "C1"}]
        arguments = {"conceptCode": "C1", "includePermissibleValues": True, "limit": 1}
        first = self.call(**arguments)
        self.ssis.values[0]["value"] = "Changed"
        result = self.call(**arguments, cursor=first["nextCursor"])
        self.assertEqual(result["error"]["code"], "cursor_expired")

    def test_value_only_pages_keep_provenance_on_the_use_not_the_top_level(self):
        self.ssis.values = [{"id": "123", "version": "1", "value": "Male", "concept": NCIT + "C1"}]
        result = self.call(conceptCode="C1", includePermissibleValues=True)
        self.assertEqual(result["dataElements"], [])
        self.assertNotIn("provenance", result)
        self.assertEqual(
            result["permissibleValues"][0]["provenance"]["upstream"]["graphs"], self.ssis.graphs
        )

    def test_pv_graph_release_mismatch_names_requested_and_served_before_reading_values(self):
        self.ssis.graphs[0]["version"] = "26.05d"
        result = self.resolve()
        self.assertEqual(result["error"]["code"], "release_mismatch")
        self.assertEqual(
            result["error"]["details"],
            {"requested": "26.06e", "served": ["26.05d"], "source": NCIT_GRAPH},
        )
        self.assertEqual(self.ssis.calls, [("identities",)])

    def test_pv_missing_graph_version_fails_closed(self):
        del self.ssis.graphs[0]["version"]
        result = self.resolve()
        self.assertEqual(result["error"]["code"], "release_not_available")
        self.assertEqual(result["error"]["details"], {"requested": "26.06e", "source": NCIT_GRAPH})
        self.assertEqual(self.ssis.calls, [("identities",)])

    def test_pv_exact_latest_value_is_case_and_whitespace_sensitive(self):
        self.ssis.element_values = [value_row(version="2.10")]
        for text in ("male", " Male", "Male "):
            with self.subTest(text=text):
                self.assertEqual(self.resolve(value=text)["error"]["code"], "not_found")
        result = self.resolve()
        self.assertEqual(result["code"], "C1")
        self.assertEqual(result["permissibleValue"]["value"], "Male")

    def test_pv_preserves_whitespace_in_an_exactly_matching_value(self):
        self.ssis.element_values = [value_row(value=" Male ")]
        result = self.resolve(value=" Male ")
        self.assertEqual(result["code"], "C1")
        self.assertEqual(result["permissibleValue"]["value"], " Male ")

    def test_pv_exactly_thousand_rows_is_not_a_cut(self):
        self.ssis.element_values = [value_row(value=f"Value {i}") for i in range(999)] + [
            value_row()
        ]
        result = self.resolve()
        self.assertEqual(result["code"], "C1")
        self.assertEqual(result["permissibleValue"]["value"], "Male")

    def test_pv_empty_data_element_has_no_matching_value(self):
        result = self.resolve()
        self.assertEqual(result["error"]["code"], "not_found")

    def test_pv_wrong_evs_concept_identity_is_an_evs_failure(self):
        self.ssis.element_values = [value_row()]
        with patch.object(self.evs, "get_concept", return_value=concept("C2", active=True)):
            result = self.resolve()
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.assertEqual(result["error"]["details"], {"surface": "evs"})

    def test_pv_preserves_raw_graphs_and_normalizes_dates_with_explicit_cache_hint(self):
        self.ssis.element_values = [value_row()]
        result = self.resolve()
        self.assertEqual(result["provenance"]["upstream"]["graphs"], self.ssis.graphs)
        self.assertEqual(
            result["provenance"]["graphs"],
            [
                {"graph": NCIT_GRAPH, "version": "26.06e", "date": "2026-06-29"},
                {"graph": CADSR_GRAPH, "date": "2026-06-01"},
            ],
        )
        self.assertEqual(self.hint, {"ttlMs": 3_600_000, "cacheScope": "public"})

    def test_pv_identifier_rejects_either_other_selector_before_upstream(self):
        for extra in ({"dataElementId": "123"}, {"value": "Male"}):
            with self.subTest(extra=extra):
                result = self.call(
                    "get_concept_for_permissible_value", permissibleValueId="456", **extra
                )
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(result["error"]["details"]["parameter"], "permissibleValueId")
        unsupported = self.call("get_concept_for_permissible_value", permissibleValueId="456")
        self.assertEqual(unsupported["error"]["code"], "capability_unavailable")
        self.assertEqual(unsupported["error"]["details"], {"capability": "OP-C10"})
        self.assertEqual(self.ssis.calls, [])
        self.assertEqual(self.evs.calls, [])

    def test_pv_major_version_precedes_minor_when_selecting_latest(self):
        self.ssis.element_values = [
            value_row(code="C1", version="1.9"),
            value_row(code="C2", version="2.0"),
        ]
        result = self.resolve()
        self.assertEqual(result["code"], "C2")
        self.assertEqual(result["permissibleValue"]["dataElement"]["version"], "2.0")

    def test_pv_malformed_version_is_a_structured_upstream_failure(self):
        self.ssis.element_values = [value_row(version="1.2.3")]
        result = self.resolve()
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.assertEqual(result["error"]["details"], {"surface": "ssis"})

    def test_ambiguous_candidates_are_sorted_under_fixed_hash_seeds(self):
        script = """
import json
from nci_si_mcp.errors import PlatformError
from nci_si_mcp.seam import NCIT_NAMESPACE, _main_code
rows = [{"concept": NCIT_NAMESPACE + code} for code in ("C9", "C2", "C10", "C1")]
try:
    _main_code(rows)
except PlatformError as error:
    print(json.dumps(error.details))
"""
        for seed in ("1", "2", "3"):
            with self.subTest(seed=seed):
                process = subprocess.run(  # noqa: S603 - fixed source in this interpreter
                    [sys.executable, "-c", script],
                    env=os.environ | {"PYTHONHASHSEED": seed},
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual(
                    json.loads(process.stdout),
                    {"surface": "ssis", "candidates": ["C1", "C10", "C2", "C9"]},
                )
