"""Cross-domain behavior, with independently supplied offline graph and EVS states."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fakes import FakeEVS, FakeSSIS, concept, release
from nci_si_mcp.caching import cache_call
from nci_si_mcp.config import Settings
from nci_si_mcp.context import Context
from nci_si_mcp.cursor import decode, encode
from nci_si_mcp.evs import EVSClient
from nci_si_mcp.registry import invoke
from nci_si_mcp.release_selection import SessionRelease, session_scope
from nci_si_mcp.ssis import CADSR_GRAPH, NCIT_GRAPH

NCIT = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"


def element(identifier, version="1"):
    return {"id": str(identifier), "version": version, "name": f"Element {identifier}"}


def value_row(value="Male", code="C1", version="1"):
    return {"version": version, "value": value} | ({"concept": NCIT + code} if code else {})


def gdc_map(code="C1", version="26.06e"):
    return {
        "mapsetCode": "NCIt_Maps_To_GDC",
        "source": "ncit",
        "target": "GDC",
        "sourceCode": code,
        "sourceTerminologyVersion": version,
        "targetCode": "diagnosis",
        "targetName": f"Stored {code}",
    }


class SeamTest(unittest.TestCase):
    def test_invalid_graph_dates_fail_without_returning_joined_content(self):
        for graph in self.ssis.graphs:
            with self.subTest(graph=graph["graph"]):
                original = graph["date"]
                graph["date"] = "not a dataset date"
                try:
                    result = self.call(conceptCode="C1", release="26.06e")
                finally:
                    graph["date"] = original
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.assertEqual(result["error"]["details"]["surface"], "ssis")
                self.assertNotIn("dataElements", result)

    def test_cursor_at_or_beyond_the_end_is_invalid_instead_of_an_empty_success(self):
        self.ssis.elements = [element(123), element(456)]
        arguments = {
            "conceptCode": "C1",
            "terminology": "ncit",
            "release": "26.06e",
            "expandDescendants": False,
            "includePermissibleValues": False,
            "limit": 1,
        }
        first = self.call(**arguments)
        applied = arguments | {"tool": "find_data_elements_for_concept"}
        position = decode(first["nextCursor"], applied, indexed=True)
        for offset in (len(self.ssis.elements), len(self.ssis.elements) + 1):
            with self.subTest(offset=offset):
                token = encode(applied, offset, position.build_id)
                result = self.call(**arguments, cursor=token)
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(result["error"]["details"]["parameter"], "cursor")
                self.assertNotIn("dataElements", result)

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = Settings(data_dir=Path(directory.name))
        self.ssis = FakeSSIS(settings)
        self.evs = FakeEVS([concept("C1", active=True), concept("C2", active=True)])
        self.context = Context(settings, evs=self.evs, ssis=self.ssis)
        self.addCleanup(patch.stopall)
        patch("nci_si_mcp.http_client._open", side_effect=AssertionError("Unexpected HTTP")).start()

    def call(self, operation="find_data_elements_for_concept", **arguments):
        with cache_call() as hint:
            result = invoke(self.context, operation, _correlation_id="seam-test", **arguments)
        self.hint = hint
        return result

    def test_graph_uses_preserve_both_states_without_fabricating_registry_release(self):
        self.ssis.elements = [element(123)]
        result = self.call(conceptCode="C1", release="26.06e")
        self.assertEqual(
            result["dataElements"][0]["dataElement"],
            {"publicId": "123", "version": "1", "longName": "Element 123"},
        )
        provenance = result["dataElements"][0]["provenance"]
        self.assertEqual(provenance["registry"], {"registry": "cadsr"})
        self.assertEqual(provenance["release"]["identifier"], "26.06e")
        self.assertEqual(
            {row["graph"]: row["date"] for row in provenance["graphs"]},
            {NCIT_GRAPH: "2026-06-29", CADSR_GRAPH: "2026-06-01"},
        )
        self.assertEqual(provenance["upstream"]["graphs"], self.ssis.graphs)
        self.assertEqual(self.hint, {"ttlMs": 3_600_000, "cacheScope": "public"})
        self.assertNotIn("permissibleValues", result)

    def test_implicit_release_uses_private_zero_cache(self):
        result = self.call(conceptCode="C1")
        self.assertEqual(result["provenance"]["release"]["identifier"], "26.06e")
        self.assertEqual(self.hint, {"ttlMs": 0, "cacheScope": "private"})
        self.assertEqual(result["dataElements"], [])
        self.assertEqual(result["truncation"], {"occurred": False})

    def test_malformed_joined_identities_and_iris_are_errors_not_invented_content(self):
        for key, value in (("id", "0"), ("id", None), ("version", "new"), ("name", "")):
            with self.subTest(key=key):
                self.ssis.elements = [element(123) | {key: value}]
                result = self.call(conceptCode="C1")
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.ssis.elements = []
        self.ssis.values = [
            {"id": "123", "version": "1", "value": "Male", "concept": "https://other.test/C1"}
        ]
        result = self.call(conceptCode="C1", includePermissibleValues=True)
        self.assertEqual(result["error"]["details"]["surface"], "ssis")

    def test_thousand_element_boundary_keeps_unqueried_values_explicitly_incomplete(self):
        self.ssis.elements = [element(i) for i in range(1, 1001)]
        result = self.call(conceptCode="C1", includePermissibleValues=True, limit=1000)
        self.assertEqual(
            result["truncation"],
            {
                "occurred": True,
                "bound": "results",
                "limit": 1000,
                "reached": 1000,
                "omitted": 0,
                "exact": False,
            },
        )
        self.assertEqual(result["permissibleValues"], [])
        self.assertFalse(any(call[0] == "values" for call in self.ssis.calls))

    def test_graph_mismatch_fails_before_content_query(self):
        self.ssis.graphs[0]["version"] = "26.05d"
        result = self.call(conceptCode="C1", release="26.06e")
        self.assertEqual(result["error"]["code"], "release_mismatch")
        self.assertEqual(result["error"]["details"]["source"], NCIT_GRAPH)
        self.assertEqual(self.ssis.calls, [("identities",)])

    def test_missing_graph_version_is_release_not_available(self):
        del self.ssis.graphs[0]["version"]
        result = self.call(conceptCode="C1", release="26.06e")
        self.assertEqual(result["error"]["code"], "release_not_available")
        self.assertEqual(self.ssis.calls, [("identities",)])

    def test_one_stream_pages_elements_then_values_with_both_arrays_present(self):
        self.ssis.elements = [element(123), element(456)]
        self.ssis.values = [{"id": "123", "version": "1", "value": "Male", "concept": NCIT + "C1"}]
        arguments = {
            "conceptCode": "C1",
            "release": "26.06e",
            "includePermissibleValues": True,
            "limit": 1,
        }
        first = self.call(**arguments)
        second = self.call(**arguments, cursor=first["nextCursor"])
        third = self.call(**arguments, cursor=second["nextCursor"])
        self.assertEqual([first["permissibleValues"], second["permissibleValues"]], [[], []])
        self.assertEqual(third["dataElements"], [])
        self.assertEqual(third["permissibleValues"][0]["conceptCode"], "C1")
        self.assertNotIn("nextCursor", third)
        self.assertTrue(
            all(page["truncation"] == {"occurred": False} for page in (first, second, third))
        )
        self.assertIn(("values", "C1", False, 998), self.ssis.calls)

    def test_data_element_cap_skips_value_query_and_reports_one_cut(self):
        self.ssis.elements = [element(i) for i in range(1, 1002)]
        result = self.call(
            conceptCode="C1", release="26.06e", includePermissibleValues=True, limit=1000
        )
        self.assertEqual(len(result["dataElements"]), 1000)
        self.assertEqual(result["permissibleValues"], [])
        self.assertEqual(
            result["truncation"],
            {
                "occurred": True,
                "bound": "results",
                "limit": 1000,
                "reached": 1000,
                "omitted": 1,
                "exact": False,
            },
        )
        self.assertNotIn("nextCursor", result)
        self.assertFalse(any(call[0] == "values" for call in self.ssis.calls))

    def test_value_pages_cross_the_join_boundary_then_continue_without_duplicates(self):
        self.ssis.elements = [element(123)]
        self.ssis.values = [
            {"id": "123", "version": "1", "value": label, "concept": NCIT + "C1"}
            for label in ("A", "B", "C", "D")
        ]
        arguments = {"conceptCode": "C1", "includePermissibleValues": True, "limit": 2}
        first = self.call(**arguments)
        second = self.call(**arguments, cursor=first["nextCursor"])
        third = self.call(**arguments, cursor=second["nextCursor"])
        pages = (first, second, third)
        self.assertEqual(
            [[row["dataElement"]["publicId"] for row in page["dataElements"]] for page in pages],
            [["123"], [], []],
        )
        self.assertEqual(
            [[row["value"] for row in page["permissibleValues"]] for page in pages],
            [["A"], ["B", "C"], ["D"]],
        )
        self.assertNotIn("nextCursor", third)
        self.assertTrue(all(page["truncation"] == {"occurred": False} for page in pages))

    def test_value_query_uses_only_the_remaining_cap_and_retains_its_sentinel(self):
        self.ssis.elements = [element(i) for i in range(1, 1000)]
        self.ssis.values = [
            {"id": "123", "version": "1", "value": label, "concept": NCIT + "C1"}
            for label in ("A", "B", "C")
        ]
        result = self.call(
            conceptCode="C1", release="26.06e", includePermissibleValues=True, limit=1000
        )
        self.assertIn(("values", "C1", False, 1), self.ssis.calls)
        self.assertEqual(len(result["permissibleValues"]), 1)
        self.assertEqual(result["truncation"]["omitted"], 1)
        self.assertFalse(result["truncation"]["exact"])

    def test_changed_snapshot_expires_cursor(self):
        self.ssis.elements = [element(123), element(456)]
        first = self.call(conceptCode="C1", release="26.06e", limit=1)
        self.ssis.elements[1]["name"] = "Changed"
        result = self.call(conceptCode="C1", release="26.06e", limit=1, cursor=first["nextCursor"])
        self.assertEqual(result["error"]["code"], "cursor_expired")

    def test_value_uses_latest_numeric_version_before_matching_text(self):
        self.ssis.element_values = [
            value_row(code="C1", version="2.9"),
            value_row(code="C2", version="2.10"),
        ]
        result = self.call(
            "get_concept_for_permissible_value", dataElementId="123", value="Male", release="26.06e"
        )
        self.assertEqual(result["code"], "C2")
        self.assertEqual(
            result["permissibleValue"],
            {"dataElement": {"publicId": "123", "version": "2.10"}, "value": "Male"},
        )
        self.assertEqual(result["provenance"]["registry"], {"registry": "cadsr"})

    def test_older_values_and_inexact_spelling_are_not_candidates(self):
        self.ssis.element_values = [
            value_row(version="2.9"),
            value_row(value="Other", version="2.10"),
            value_row(value="Female", version="2.10"),
        ]
        for text in ("Male", "male", "female", " Female", "Female "):
            with self.subTest(text=text):
                result = self.call(
                    "get_concept_for_permissible_value",
                    dataElementId="123",
                    value=text,
                    release="26.06e",
                )
                self.assertEqual(result["error"]["code"], "not_found")
        exact = self.call(
            "get_concept_for_permissible_value",
            dataElementId="123",
            value="Female",
            release="26.06e",
        )
        self.assertEqual(exact["permissibleValue"]["value"], "Female")

    def test_conflicting_or_missing_main_concepts_name_ambiguous_registry_data(self):
        for rows, candidates in (
            ([value_row(code="C1"), value_row(code="C2")], ["C1", "C2"]),
            ([value_row(code=None)], []),
            ([value_row(code=None), value_row()], ["C1"]),
        ):
            with self.subTest(candidates=candidates):
                self.ssis.element_values = rows
                result = self.call(
                    "get_concept_for_permissible_value",
                    dataElementId="123",
                    value="Male",
                    release="26.06e",
                )
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.assertEqual(
                    result["error"]["details"], {"surface": "ssis", "candidates": candidates}
                )
                self.assertIn("ambiguous", result["error"]["message"])
                self.assertNotIn("retry", result["error"]["message"].lower())

    def test_identical_main_rows_do_not_create_ambiguity(self):
        self.ssis.element_values = [value_row(), value_row()]
        result = self.call(
            "get_concept_for_permissible_value", dataElementId="123", value="Male", release="26.06e"
        )
        self.assertEqual(result["code"], "C1")

    def test_latest_version_without_values_never_falls_back_to_an_older_one(self):
        self.ssis.element_values = [value_row(version="2.9"), {"version": "2.10"}]
        result = self.call("get_concept_for_permissible_value", dataElementId="123", value="Male")
        self.assertEqual(result["error"]["code"], "not_found")

    def test_cut_value_list_never_selects_a_latest_version_from_partial_rows(self):
        self.ssis.element_values = [value_row(version="2.9")] * 1001
        result = self.call("get_concept_for_permissible_value", dataElementId="123", value="Male")
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.assertEqual(result["error"]["details"]["surface"], "ssis")
        self.assertIn("more value rows than the bound", result["error"]["message"])

    def test_permissible_value_identifier_is_explicitly_unsupported(self):
        result = self.call(
            "get_concept_for_permissible_value", permissibleValueId="123", release="26.06e"
        )
        self.assertEqual(result["error"]["code"], "capability_unavailable")
        self.assertEqual(self.ssis.calls, [])

    def test_invalid_selectors_fail_before_any_upstream_request(self):
        cases = [
            ("find_data_elements_for_concept", {"conceptCode": 'C1>"}'}),
            ("find_data_elements_for_concept", {"conceptCode": "C1", "limit": 0}),
            ("find_data_elements_for_concept", {"conceptCode": "C1", "expandDescendants": 1}),
            (
                "find_data_elements_for_concept",
                {"conceptCode": "C1", "includePermissibleValues": 1},
            ),
            ("find_data_elements_for_concept", {"conceptCode": "C1", "terminology": "mdr"}),
            ("find_data_elements_for_concept", {"conceptCode": "C1", "cursor": "bad"}),
            ("get_concept_for_permissible_value", {"dataElementId": '12>"}', "value": "x"}),
            ("get_concept_for_permissible_value", {"value": "x"}),
            ("get_concept_for_permissible_value", {"dataElementId": "12"}),
            (
                "get_concept_for_permissible_value",
                {"dataElementId": "12", "permissibleValueId": "13", "value": "x"},
            ),
            ("resolve_stored_value", {"conceptCode": "C1", "commons": " "}),
            (
                "resolve_stored_value",
                {"conceptCode": "C1", "commons": "PDC", "dataElementId": "bad"},
            ),
            ("get_release_alignment", {"maxIntervalDays": True}),
            ("get_release_alignment", {"maxIntervalDays": -1}),
        ]
        for operation, arguments in cases:
            with self.subTest(operation=operation, arguments=arguments):
                self.assertEqual(
                    self.call(operation, **arguments)["error"]["code"], "invalid_request"
                )
        self.assertEqual(self.ssis.calls, [])
        self.assertEqual(self.evs.calls, [])

    def test_gdc_selector_is_not_silently_ignored(self):
        result = self.call(
            "resolve_stored_value", conceptCode="C1", commons="GDC", dataElementId="123"
        )
        self.assertEqual(result["error"]["code"], "capability_unavailable")
        self.assertEqual(self.evs.calls, [])

    def test_gdc_keeps_only_exact_concept_maps_and_counts_returned_values(self):
        self.context.evs = EVSClient("https://evs.test")
        mapset = {"code": "NCIt_Maps_To_GDC", "version": "26.06e"}
        common = {
            "mapsetCode": mapset["code"],
            "source": "ncit",
            "target": "GDC",
            "sourceTerminologyVersion": "26.06e",
            "targetCode": "diagnosis",
            "licenseText": "Upstream map attribution",
        }
        rows = [
            common | {"sourceCode": code, "targetName": label}
            for code, label in (("C4817", "Ewing tumor"), ("C48177", "processing"))
        ]
        with patch.object(
            self.context.evs,
            "_get_existing",
            side_effect=[self.evs.get_terminologies("ncit"), mapset, {"total": 2, "maps": rows}],
        ):
            result = self.call(
                "resolve_stored_value", conceptCode="C4817", commons="GDC", release="26.06e"
            )
        self.assertEqual(
            [(row["value"], row["field"]) for row in result["storedValues"]],
            [("Ewing tumor", "diagnosis")],
        )
        self.assertEqual(
            result["evidence"],
            {
                "sources": [{"mapset": mapset["code"], "version": "26.06e"}],
                "valueLevelBinding": True,
                "coverage": 1,
            },
        )
        self.assertEqual(result["confidence"], "asserted")
        self.assertEqual(
            result["storedValues"][0]["provenance"]["attribution"], "Upstream map attribution"
        )

    def test_gdc_malformed_licence_text_is_not_silently_discarded(self):
        self.context.evs = EVSClient("https://evs.test")
        mapset = {"code": "NCIt_Maps_To_GDC", "version": "26.06e", "licenseText": {"bad": "shape"}}
        with patch.object(
            self.context.evs, "_get_existing", side_effect=[self.evs.get_terminologies(), mapset]
        ):
            result = self.call(
                "resolve_stored_value", conceptCode="C1", commons="GDC", release="26.06e"
            )
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.assertEqual(result["error"]["details"]["surface"], "evs")

    def test_gdc_reads_later_pages_before_filtering_and_counting_exact_matches(self):
        self.context.evs = EVSClient("https://evs.test")
        pages = [
            {"total": 11, "maps": [gdc_map(f"C1{i}") for i in range(10)]},
            {"total": 11, "maps": [gdc_map()]},
        ]
        with patch.object(
            self.context.evs,
            "_get_existing",
            side_effect=[
                self.evs.get_terminologies(),
                {"code": "NCIt_Maps_To_GDC", "version": "26.06e"},
                *pages,
            ],
        ) as get:
            result = self.call(
                "resolve_stored_value", conceptCode="C1", commons="GDC", release="26.06e"
            )
        self.assertEqual([row["value"] for row in result["storedValues"]], ["Stored C1"])
        self.assertEqual(result["evidence"]["coverage"], 1)
        self.assertEqual([call.args[1]["fromRecord"] for call in get.call_args_list[-2:]], [0, 10])

    def test_gdc_changing_totals_and_over_bound_results_fail_without_partial_success(self):
        first_page = [gdc_map() for _ in range(10)]
        cases = (
            (
                [{"total": 11, "maps": first_page}, {"total": 12, "maps": [gdc_map()] * 2}],
                "upstream_unavailable",
            ),
            ([{"total": 1001, "maps": first_page}], "bound_exceeded"),
        )
        self.context.evs = EVSClient("https://evs.test")
        for pages, expected in cases:
            with (
                self.subTest(expected=expected),
                patch.object(
                    self.context.evs,
                    "_get_existing",
                    side_effect=[
                        self.evs.get_terminologies(),
                        {"code": "NCIt_Maps_To_GDC", "version": "26.06e"},
                        *pages,
                    ],
                ),
            ):
                result = self.call(
                    "resolve_stored_value", conceptCode="C1", commons="GDC", release="26.06e"
                )
                self.assertEqual(result["error"]["code"], expected)
                self.assertNotIn("storedValues", result)

    def test_gdc_individual_maps_must_name_the_requested_release(self):
        self.context.evs = EVSClient("https://evs.test")
        for version in ("26.05d", None):
            with (
                self.subTest(version=version),
                patch.object(
                    self.context.evs,
                    "_get_existing",
                    side_effect=[
                        self.evs.get_terminologies(),
                        {"code": "NCIt_Maps_To_GDC", "version": "26.06e"},
                        {"total": 1, "maps": [gdc_map(version=version)]},
                    ],
                ),
            ):
                result = self.call(
                    "resolve_stored_value", conceptCode="C1", commons="GDC", release="26.06e"
                )
                self.assertEqual(result["error"]["code"], "release_mismatch")
                self.assertEqual(result["error"]["details"]["served"], [version or "unknown"])

    def test_gdc_checks_mapset_release_before_requesting_maps(self):
        self.context.evs = EVSClient("https://evs.test")
        with patch.object(
            self.context.evs,
            "_get_existing",
            side_effect=[
                self.evs.get_terminologies("ncit"),
                {"code": "NCIt_Maps_To_GDC", "version": "26.05d"},
            ],
        ):
            result = self.call(
                "resolve_stored_value", conceptCode="C1", commons="GDC", release="26.06e"
            )
        self.assertEqual(result["error"]["code"], "release_mismatch")
        self.assertEqual(result["error"]["details"]["served"], ["26.05d"])

    def crosswalk(self):
        return [
            {
                "CDE Public ID": identifier,
                "Version": "1",
                "CRDC Name": "sex",
                "Used By": users,
                "permissibleValues": [{"Permissible Value": "Male", "Concept Code": "C9:C1"}],
            }
            for identifier, users in (
                ("123", "Other, Pediatric Cancer"),
                ("456", "Pediatric Cancer Extra"),
            )
        ]

    def test_crosswalk_matches_complete_context_and_colon_separated_binding(self):
        with patch.object(self.context.cadsr, "get_crdc_list", return_value=self.crosswalk()):
            result = self.call(
                "resolve_stored_value",
                conceptCode="C1",
                commons="Pediatric Cancer",
                release="26.06e",
            )
        self.assertEqual(
            [
                (row["value"], row["field"], row["source"]["dataElement"]["publicId"])
                for row in result["storedValues"]
            ],
            [("Male", "sex", "123")],
        )
        self.assertEqual(result["evidence"]["coverage"], 1)
        self.assertTrue(result["evidence"]["valueLevelBinding"])

    def test_crosswalk_selector_and_missing_binding_return_honest_empty_results(self):
        for commons, identifier in (("PDC", None), ("Pediatric Cancer", "456")):
            with (
                self.subTest(commons=commons),
                patch.object(self.context.cadsr, "get_crdc_list", return_value=self.crosswalk()),
            ):
                result = self.call(
                    "resolve_stored_value",
                    conceptCode="C1",
                    commons=commons,
                    dataElementId=identifier,
                    release="26.06e",
                )
                self.assertEqual(result["storedValues"], [])
                self.assertEqual(result["confidence"], "none")
                self.assertEqual(
                    result["evidence"], {"sources": [], "valueLevelBinding": False, "coverage": 0}
                )
                self.assertEqual(result["provenance"]["registry"], {"registry": "cadsr"})

    def test_bound_value_without_a_crdc_field_name_is_an_upstream_error(self):
        rows = self.crosswalk()
        rows[0]["CRDC Name"] = None
        with patch.object(self.context.cadsr, "get_crdc_list", return_value=rows):
            result = self.call("resolve_stored_value", conceptCode="C1", commons="Pediatric Cancer")
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.assertEqual(result["error"]["details"]["surface"], "cadsr")

    def test_alignment_warns_only_above_threshold_and_preserves_graph_difference(self):
        interval = 28
        self.ssis.graphs[0]["version"] = "26.05d"
        listing = '<pre><a href="releasedCDEsXML-OD.zip">export</a> 2026-06-05 12:30\n</pre>'
        for threshold in (27, 28, 29):
            with (
                self.subTest(threshold=threshold),
                patch.object(self.context.cadsr.export_http, "get_text", return_value=listing),
            ):
                result = self.call("get_release_alignment", maxIntervalDays=threshold)
                self.assertEqual(result["intervalDays"], 28)
                self.assertEqual("warning" in result, threshold < interval)
                self.assertEqual(self.hint, {"ttlMs": 0, "cacheScope": "public"})
                datasets = {row["name"]: row for row in result["datasets"]}
                self.assertEqual(datasets["ssis_ncit_graph"]["version"], "26.05d")
                self.assertEqual(
                    datasets["ssis_ncit_graph"]["provenance"]["release"]["identifier"], "26.05d"
                )
                self.assertNotIn("version", datasets["cadsr_export"])
                self.assertEqual(datasets["cadsr_export"]["date"], "2026-06-05")

    def test_alignment_reads_current_evs_without_using_or_changing_a_session_pin(self):
        held = release("25.01d", "2025-01-27")
        state = SessionRelease(selected=held)
        listing = '<pre><a href="releasedCDEsXML-OD.zip">export</a> 2026-06-05 12:30\n</pre>'
        with (
            session_scope(state),
            patch.object(self.context.cadsr.export_http, "get_text", return_value=listing),
        ):
            result = self.call("get_release_alignment")
        ncit = next(row for row in result["datasets"] if row["name"] == "ncit")
        self.assertEqual((ncit["version"], ncit["date"]), ("26.06e", "2026-06-29"))
        self.assertEqual(state.selected, held)

    def test_alignment_does_not_select_the_first_implicit_session_content_release(self):
        state = SessionRelease()
        listing = '<pre><a href="releasedCDEsXML-OD.zip">export</a> 2026-06-05 12:30\n</pre>'
        with (
            session_scope(state),
            patch.object(self.context.cadsr.export_http, "get_text", return_value=listing),
        ):
            result = self.call("get_release_alignment")
        self.assertEqual(result["datasets"][0]["version"], "26.06e")
        self.assertIsNone(state.selected)

    def test_alignment_preserves_the_evs_identity_as_upstream_evidence(self):
        listing = '<pre><a href="releasedCDEsXML-OD.zip">export</a> 2026-06-05 12:30\n</pre>'
        with patch.object(self.context.cadsr.export_http, "get_text", return_value=listing):
            result = self.call("get_release_alignment")
        ncit = next(row for row in result["datasets"] if row["name"] == "ncit")
        upstream = ncit["provenance"]["upstream"]
        self.assertEqual(
            (upstream["terminology"], upstream["version"], upstream["date"]),
            ("ncit", "26.06e", "2026-06-29"),
        )

    def test_alignment_unreadable_dates_name_the_surface_that_supplied_them(self):
        listing = '<pre><a href="releasedCDEsXML-OD.zip">export</a> 2026-06-05 12:30\n</pre>'
        self.evs.release = release("26.06e", "not a date")
        with patch.object(self.context.cadsr.export_http, "get_text", return_value=listing):
            result = self.call("get_release_alignment")
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.assertEqual(result["error"]["details"]["surface"], "evs")

        self.evs.release = release("26.06e", "2026-06-29")
        stale = SimpleNamespace(generated_at="not a date", source_distribution=None)
        with (
            patch.object(self.context.cadsr.export_http, "get_text", return_value=listing),
            patch("nci_si_mcp.seam.export_state", return_value=stale),
        ):
            result = self.call("get_release_alignment")
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.assertEqual(result["error"]["details"]["surface"], "cadsr")

    def test_alignment_missing_ncit_graph_version_never_becomes_a_registry_identity(self):
        del self.ssis.graphs[0]["version"]
        listing = '<pre><a href="releasedCDEsXML-OD.zip">export</a> 2026-06-05 12:30\n</pre>'
        with patch.object(self.context.cadsr.export_http, "get_text", return_value=listing):
            result = self.call("get_release_alignment")
        self.assertEqual(result["error"]["code"], "release_not_available")
        self.assertEqual(result["error"]["details"]["source"], NCIT_GRAPH)
