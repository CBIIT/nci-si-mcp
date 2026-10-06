"""Phase 2 adapter contracts, including their independent mutation-review gaps."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import yaml
from pydantic import TypeAdapter

from fakes import concept
from nci_si_mcp import results
from nci_si_mcp.caching import cache_call
from nci_si_mcp.context import Context
from nci_si_mcp.errors import IndexEvaluationError, IndexStateError, correlated
from nci_si_mcp.http_client import UpstreamTooLargeError, UpstreamUnavailableError
from nci_si_mcp.models import TraversalProvenance
from nci_si_mcp.registry import invoke
from test_evs_client import FakeResponse, http_error
from test_server import ServerFixture

ROOT = Path(__file__).resolve().parents[1]
ITEM_TYPES = {
    "concept": "Concept",
    "node": "Node",
    "edge": "Edge",
    "subset": "Subset",
    "mapping": "Mapping",
    "replacement": "Replacement",
    "relationship": "CatalogueRelationship",
    "search_result": "RankedConcept",
    "member": "ValueSetMember",
    "terminology": "TerminologyResult",
    "index_manifest": "IndexManifestResult",
    "error": "ErrorRecord",
}


class RecordReviewTest(TestCase):
    def test_phase_two_item_keys_and_optionality_match_specification(self):
        records = yaml.safe_load((ROOT / "spec/records.yaml").read_text())
        for name, type_name in ITEM_TYPES.items():
            with self.subTest(record=name):
                fields = records[name]["fields"]
                schema = TypeAdapter(getattr(results, type_name)).json_schema()
                required = {key for key, field in fields.items() if not field.get("optional")}
                self.assertEqual(set(schema["properties"]), set(fields))
                self.assertEqual(set(schema["required"]), required)

    def test_catalogue_closed_values_match_specification(self):
        fields = yaml.safe_load((ROOT / "spec/records.yaml").read_text())["relationship"]["fields"]
        schema = TypeAdapter(results.CatalogueRelationship).json_schema()
        for key in ("kind", "polarity"):
            with self.subTest(key=key):
                self.assertEqual(set(schema["properties"][key]["enum"]), set(fields[key]["values"]))

    def test_list_envelopes_preserve_required_counts_and_missing_identifiers(self):
        tools = yaml.safe_load((ROOT / "spec/tools.yaml").read_text())
        for tool, record, field in (
            ("get_concepts", results.ConceptBatch, "missing"),
            ("expand_value_set", results.ValueSetExpansion, "total"),
        ):
            with self.subTest(tool=tool):
                self.assertIn(field, tools[tool]["returns"])
                self.assertNotIn(field + "?", tools[tool]["returns"])
                self.assertIn(field, TypeAdapter(record).json_schema()["required"])
        # The specification permits omission; this implementation guarantees the count.
        self.assertIn("totalKnown", TypeAdapter(results.ConceptSearch).json_schema()["required"])

    def test_empty_qualifiers_and_evidence_pass_through_unchanged(self):
        for empty in ({}, [], "", False, 0):
            with self.subTest(empty=empty):
                record = TraversalProvenance(
                    release={"terminology": "ncit", "identifier": "26.06e"},
                    source="evs_rest",
                    served_by="live",
                    retrieved_at="2026-10-01T00:00:00Z",
                    correlation_id="test",
                    depth=1,
                    qualifiers=empty,
                    evidence=empty,
                ).to_dict()
                self.assertEqual(record["qualifiers"], empty)
                self.assertEqual(record["evidence"], empty)


class AdapterReviewTest(ServerFixture):
    def candidate(self):
        return self.context.index.build(
            [concept("C1", "One", active=True)], None, self.context.embedding_provider
        )

    def test_three_pinned_content_tools_select_long_public_cache_hints(self):
        for tool, arguments in (
            ("list_relationships", {}),
            ("get_concepts", {"codes": ["C3262"]}),
            ("get_concept_hierarchy", {"code": "C3262", "direction": "child"}),
        ):
            with self.subTest(tool=tool), cache_call() as hint:
                result = invoke(
                    self.context, tool, terminology="ncit", release="26.06e", **arguments
                )
                self.assertNotIn("error", result)
                self.assertEqual(hint, {"ttlMs": 86_400_000, "cacheScope": "public"})

    def fhir_context(self):
        return Context(
            replace(self.settings, evs_max_attempts=2, evs_max_response_bytes=8),
            evs=self.evs,
            index=self.context.index,
            embedding_provider=self.context.embedding_provider,
        )

    def test_fhir_retries_use_configured_attempts_and_surface_label(self):
        context = self.fhir_context()
        with (
            patch("nci_si_mcp.http_client._open", side_effect=lambda *args: http_failure()),
            patch("nci_si_mcp.http_client.time.sleep"),
            self.assertRaises(UpstreamUnavailableError) as raised,
        ):
            context.fhir.get_json("/ValueSet/$expand")
        self.assertEqual(raised.exception.details["attempts"], 2)
        self.assertEqual(raised.exception.details["surface"], "evs_fhir")

    def test_fhir_response_limit_uses_configured_bytes(self):
        context = self.fhir_context()
        with (
            patch("nci_si_mcp.http_client._open", return_value=FakeResponse(b'{"large":"value"}')),
            self.assertRaises(UpstreamTooLargeError) as raised,
        ):
            context.fhir.get_json("/ValueSet/$expand")
        self.assertEqual(raised.exception.details["limit"], 8)
        self.assertEqual(raised.exception.details["reached"], 9)
        self.assertEqual(raised.exception.details["bound"], "NCI_SI_EVS_MAX_RESPONSE_BYTES")

    def test_missing_index_names_unavailable_search_capability(self):
        result = invoke(self.context, "search_concepts", "ncit", "26.06e", "One", mode="semantic")
        self.assertEqual(result["error"]["code"], "capability_unavailable")
        self.assertEqual(
            result["error"]["details"],
            {"capability": "semantic/hybrid search without an active NCIt index"},
        )

    def test_absent_index_resource_names_its_capability(self):
        result = invoke(self.context, "index_resource", "26.06e")
        self.assertEqual(result["error"]["code"], "capability_unavailable")
        self.assertEqual(result["error"]["details"], {"capability": "NCIt index"})

    def test_activate_returns_selected_build_and_its_manifest(self):
        candidate = self.candidate()
        result = invoke(self.context, "index_activate", candidate.build_id)
        active = self.context.index.get_active_manifest()
        with correlated(result["manifest"]["provenance"]["correlationId"]):
            self.assertEqual(
                result, {"buildId": candidate.build_id, "manifest": active.to_result()}
            )
        self.assertEqual(active.build_id, candidate.build_id)

    def test_sample_rebuild_has_exact_ungated_build_shape(self):
        candidate = self.candidate()
        result = invoke(self.context, "index_rebuild", candidate.build_id)
        rebuilt = next(
            item for item in self.context.index.list_builds() if item.build_id == result["buildId"]
        )
        with correlated(result["manifest"]["provenance"]["correlationId"]):
            self.assertEqual(result, {"buildId": rebuilt.build_id, "manifest": rebuilt.to_result()})
        self.assertIsNone(rebuilt.evaluation_version)

    def test_sample_and_legacy_evaluation_are_ungated_and_name_dataset_version(self):
        candidate = self.candidate()
        dataset = SimpleNamespace(version="review-dataset", queries=[])
        report = SimpleNamespace(to_dict=lambda: {"mode": "semantic", "hit_at_1": 0.5})
        for kind in ("sample", "legacy"):
            with (
                self.subTest(kind=kind),
                patch("nci_si_mcp.handlers.production_set", return_value=dataset),
                patch("nci_si_mcp.handlers.evaluate_retrieval", return_value=[report]),
                patch.object(
                    self.context.index,
                    "evaluation_inputs",
                    return_value=(replace(candidate, build_kind=kind), ["C99"]),
                ),
            ):
                result = invoke(self.context, "evaluate", candidate.build_id)
                self.assertEqual(
                    result,
                    {
                        "build_id": candidate.build_id,
                        "evaluation_version": "review-dataset",
                        "gate_applies": False,
                        "results": [{"mode": "semantic", "hit_at_1": 0.5}],
                        "gold_codes_not_indexed": ["C99"],
                    },
                )

    def test_operator_failures_include_actionable_next_steps(self):
        for error, ending in (
            (
                IndexEvaluationError,
                "Inspect `index-builds` and resolve the reported evaluation problem "
                "before retrying.",
            ),
            (
                IndexStateError,
                "Inspect `index-builds` and retry against an available completed build.",
            ),
        ):
            with (
                self.subTest(error=error),
                patch.object(self.context.index, "activate", side_effect=error("Rejected build.")),
            ):
                result = invoke(self.context, "index_activate", "a" * 32)
                self.assertEqual(result["error"]["code"], "internal_error")
                self.assertTrue(result["error"]["message"].endswith(ending))


def http_failure():
    raise http_error(503, b"{}")
