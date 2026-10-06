"""Mutation regressions for stored-value evidence and independent dataset state."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fakes import FakeEVS, FakeSSIS, release, terminology_row
from nci_si_mcp.bounds import Budget
from nci_si_mcp.cadsr import EXPORT_FOLDER
from nci_si_mcp.config import Settings
from nci_si_mcp.context import Context
from nci_si_mcp.evs import EVSClient
from nci_si_mcp.registry import invoke
from nci_si_mcp.ssis import SSISClient
from test_evs_client import FakeResponse
from test_seam import gdc_map


def crosswalk_row(identifier, bindings):
    return {
        "CDE Public ID": str(identifier),
        "Version": "1",
        "CRDC Name": "diagnosis",
        "Used By": "PDC",
        "permissibleValues": [
            {"Permissible Value": value, "Concept Code": code} for value, code in bindings
        ],
    }


class SeamStateMutationTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = Settings(data_dir=Path(directory.name))
        self.evs = FakeEVS()
        self.ssis = FakeSSIS(settings)
        self.context = Context(settings, evs=self.evs, ssis=self.ssis)
        opened = patch(
            "nci_si_mcp.http_client._open", side_effect=AssertionError("Unexpected HTTP")
        )
        opened.start()
        self.addCleanup(opened.stop)

    def stored(self, commons="PDC", **arguments):
        return invoke(
            self.context,
            "resolve_stored_value",
            conceptCode="C1",
            commons=commons,
            release="26.06e",
            **arguments,
        )

    def gdc(self, rows, total=None):
        self.context.evs = EVSClient("https://evs.test")
        count = len(rows) if total is None else total
        pages = [
            {"total": count, "maps": rows[start : start + 10]} for start in range(0, len(rows), 10)
        ]
        with patch.object(
            self.context.evs,
            "_get_existing",
            side_effect=[
                self.evs.get_terminologies(),
                {"code": "NCIt_Maps_To_GDC", "version": "26.06e"},
                *pages,
            ],
        ):
            return self.stored("GDC")

    def alignment(
        self,
        ncit="2026-06-01",
        graph="2026-06-01",
        cadsr="2026-06-01",
        export="2026-06-01",
        **arguments,
    ):
        self.evs.release = release("26.06e", ncit)
        self.ssis.graphs[0]["date"] = graph
        self.ssis.graphs[1]["date"] = cadsr
        listing = f'<pre><a href="releasedCDEsXML-OD.zip">export</a> {export} 12:30\n</pre>'
        with patch.object(self.context.cadsr.export_http, "get_text", return_value=listing) as get:
            result = invoke(self.context, "get_release_alignment", **arguments)
        self.export_path = get.call_args.args[0]
        return result

    def test_crosswalk_exact_colon_tokens_preserve_all_values_and_count_values_not_sources(self):
        rows = [
            crosswalk_row(123, [("first", "C1:C9"), ("second", "C9:C1"), ("prefix", "C10:C11")]),
            crosswalk_row(456, [("third", "C1"), ("fourth", "C1")]),
            crosswalk_row(789, [("unrelated", "C10")]),
        ]
        with patch.object(self.context.cadsr, "get_crdc_list", return_value=rows) as read:
            result = self.stored()
        self.assertEqual(
            [value["value"] for value in result["storedValues"]],
            ["first", "second", "third", "fourth"],
        )
        self.assertEqual(
            result["evidence"],
            {
                "sources": [
                    {"crosswalk": "CRDC", "dataElement": {"publicId": str(i), "version": "1"}}
                    for i in (123, 456, 789)
                ],
                "coverage": 4,
                "valueLevelBinding": True,
            },
        )
        self.assertEqual(result["confidence"], "asserted")
        self.assertNotIn("provenance", result)
        self.assertIsNone(read.call_args.kwargs["registry_release"])
        self.assert_crosswalk_provenance(result["storedValues"])

    def assert_crosswalk_provenance(self, values):
        for value in values:
            provenance = value["provenance"]
            self.assertEqual(
                provenance["release"],
                {"terminology": "ncit", "identifier": "26.06e", "date": "2026-06-29"},
            )
            self.assertEqual(provenance["registry"], {"registry": "cadsr"})
            self.assertEqual(provenance["source"], "cadsr_rest")
            self.assertIn("/DataElements/getCRDCList", provenance["sourceUri"])

    def test_matching_crosswalk_without_matching_values_retains_its_evidence(self):
        for bindings, binding in (([("prefix", "C10:C11")], True), ([], False)):
            with (
                self.subTest(binding=binding),
                patch.object(
                    self.context.cadsr, "get_crdc_list", return_value=[crosswalk_row(123, bindings)]
                ),
            ):
                result = self.stored()
                self.assertEqual(result["storedValues"], [])
                self.assertEqual(result["confidence"], "none")
                self.assertEqual(
                    result["evidence"],
                    {
                        "sources": [
                            {
                                "crosswalk": "CRDC",
                                "dataElement": {"publicId": "123", "version": "1"},
                            }
                        ],
                        "coverage": 0,
                        "valueLevelBinding": binding,
                    },
                )
                self.assertEqual(result["provenance"]["registry"], {"registry": "cadsr"})

    def test_whitespace_only_crosswalk_field_is_malformed(self):
        row = crosswalk_row(123, [("literal", "C1")]) | {"CRDC Name": "  "}
        with patch.object(self.context.cadsr, "get_crdc_list", return_value=[row]):
            result = self.stored()
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.assertEqual(result["error"]["details"]["surface"], "cadsr")

    def test_gdc_rejects_malformed_matching_map_identity_and_literal_types(self):
        changes = [
            ("mapsetCode", "other"),
            ("source", "other"),
            ("target", "other"),
            ("targetCode", 7),
            ("targetName", None),
        ]
        for key, value in changes:
            with self.subTest(key=key):
                result = self.gdc([gdc_map() | {key: value}])
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.assertEqual(result["error"]["details"]["surface"], "evs")
                self.assertNotIn("storedValues", result)

    def test_malformed_nonmatching_source_code_is_not_silently_filtered(self):
        for code in (None, 12, "not-a-code", "C0", "C1 "):
            with self.subTest(code=code):
                bad = gdc_map("C10")
                bad.pop("sourceCode")
                if code is not None:
                    bad["sourceCode"] = code
                result = self.gdc([gdc_map(), bad])
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.assertEqual(result["error"]["details"]["surface"], "evs")

    def test_gdc_exactly_thousand_rows_is_complete_and_overflow_details_are_truthful(self):
        result = self.gdc([gdc_map() for _ in range(1000)])
        self.assertEqual(len(result["storedValues"]), 1000)
        self.assertEqual(result["evidence"]["coverage"], 1000)
        self.assertNotIn("provenance", result)
        result = self.gdc([gdc_map() for _ in range(10)], total=1001)
        self.assertEqual(result["error"]["code"], "bound_exceeded")
        self.assertEqual(
            result["error"]["details"], {"bound": "results", "limit": 1000, "reached": 10}
        )

    def test_gdc_literal_provenance_names_the_mapset_and_retains_its_row(self):
        row = gdc_map() | {"targetName": " literal "}
        result = self.gdc([row])
        value = result["storedValues"][0]
        self.assertEqual(value["value"], " literal ")
        self.assertEqual(value["provenance"]["upstream"], row)
        self.assertEqual(value["provenance"]["source"], "evs_rest")
        self.assertEqual(
            value["provenance"]["sourceUri"], "https://evs.test/api/v1/mapset/NCIt_Maps_To_GDC"
        )

    def test_invalid_stored_concept_is_rejected_before_any_request(self):
        for code in ("C", "0", "C01", 'C1>"}'):
            with self.subTest(code=code):
                result = invoke(
                    self.context, "resolve_stored_value", conceptCode=code, commons="GDC"
                )
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(result["error"]["details"]["parameter"], "conceptCode")
        self.assertEqual(self.evs.calls, [])
        self.assertEqual(self.ssis.calls, [])

    def test_both_state_tools_share_discovery_and_content_request_allowance(self):
        self.context.evs = EVSClient("https://evs.test", max_attempts=1)
        self.context.ssis = SSISClient(self.context.settings)
        for operation, arguments in (
            ("resolve_stored_value", {"conceptCode": "C1", "commons": "GDC"}),
            ("get_release_alignment", {}),
        ):
            with (
                self.subTest(operation=operation),
                patch("nci_si_mcp.seam.Budget", return_value=Budget(requests=1)),
                patch(
                    "nci_si_mcp.http_client._open",
                    side_effect=[FakeResponse(json.dumps([terminology_row()]).encode())],
                ) as opened,
            ):
                result = invoke(self.context, operation, **arguments)
                self.assertEqual(result["error"]["code"], "bound_exceeded")
                self.assertEqual(
                    result["error"]["details"], {"bound": "requests", "limit": 1, "reached": 1}
                )
                self.assertEqual(opened.call_count, 1)

    def test_alignment_accepts_zero_and_warns_for_a_single_day_difference(self):
        result = self.alignment(maxIntervalDays=0)
        self.assertEqual(result["intervalDays"], 0)
        self.assertNotIn("warning", result)
        result = self.alignment(export="2026-06-02", maxIntervalDays=0)
        self.assertEqual(result["intervalDays"], 1)
        self.assertIn("1 days", result["warning"])
        self.assertIn("maxIntervalDays 0", result["warning"])

    def test_alignment_default_threshold_is_31_and_warning_names_observed_interval(self):
        result = self.alignment(export="2026-07-02")
        self.assertEqual(result["intervalDays"], 31)
        self.assertNotIn("warning", result)
        result = self.alignment(export="2026-07-03")
        self.assertEqual(result["intervalDays"], 32)
        self.assertIn("32 days", result["warning"])
        self.assertIn("maxIntervalDays 31", result["warning"])

    def test_each_dataset_can_determine_the_largest_interval(self):
        for dataset in ("ncit", "graph", "cadsr", "export"):
            with self.subTest(dataset=dataset):
                result = self.alignment(**{dataset: "2026-05-01"})
                self.assertEqual(result["intervalDays"], 31)
                result = self.alignment(**{dataset: "2026-07-10"})
                self.assertEqual(result["intervalDays"], 39)

    def test_alignment_registry_states_preserve_export_evidence_without_invented_release(self):
        result = self.alignment()
        self.assertEqual(self.export_path, "/CDE/XML/")
        datasets = {row["name"]: row for row in result["datasets"]}
        self.assertEqual(
            set(datasets), {"ncit", "ssis_ncit_graph", "ssis_cadsr_graph", "cadsr_export"}
        )
        for name in ("ssis_cadsr_graph", "cadsr_export"):
            self.assertEqual(datasets[name]["provenance"]["release"], {"registry": "cadsr"})
        export = datasets["cadsr_export"]
        self.assertNotIn("version", export)
        self.assertEqual(
            export["provenance"]["upstream"],
            {"generatedAt": "2026-06-01T12:30", "sourceDistribution": "releasedCDEsXML-OD.zip"},
        )
        self.assertEqual(
            export["provenance"]["sourceUri"], self.context.cadsr.export_http.url(EXPORT_FOLDER)
        )
        self.assertEqual(export["provenance"]["source"], "cadsr_export")
