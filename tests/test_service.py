import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fakes import FakeEVS, concept, release

from nci_si_mcp import service as service_module
from nci_si_mcp.config import Settings
from nci_si_mcp.embeddings import HashingEmbeddingProvider
from nci_si_mcp.errors import IndexBuildError
from nci_si_mcp.evs import (
    LOOKUP_INCLUDE,
    EVSClient,
    EVSResponseError,
    EVSResponseTooLargeError,
    EVSUnavailableError,
    ReleaseResolutionError,
)
from nci_si_mcp.index import LocalIndex
from nci_si_mcp.service import NCISIService

NEOPLASM = concept(
    "C3262",
    "Neoplasm",
    synonyms=[{"name": "Tumor"}],
    parents=[{"code": "C2991", "name": "Disease or Disorder"}],
    children=[{"code": "C4741", "name": "Neoplasm by Morphology"}],
    roles=[{"type": "Disease_Has_Abnormal_Cell", "relatedCode": "C12922", "relatedName": "Neoplastic Cell"}],
    associations=[{"type": "Concept_In_Subset", "relatedCode": "C165258", "relatedName": "A Subset"}],
)
KINASE = concept("C40704", "Receptor Tyrosine Kinase Inhibition")


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.evs = FakeEVS([NEOPLASM, KINASE])
        self.service = self.make_service()

    def make_service(self, provider=None, **settings):
        return NCISIService(
            Settings(data_dir=self.path, **settings),
            evs=self.evs,
            index=LocalIndex(self.path),
            embedding_provider=provider or HashingEmbeddingProvider(),
        )

    def index(self, *codes):
        result = self.service.index_codes(codes or ["C3262", "C40704"])
        self.assertNotIn("isError", result)
        return result

    def assertError(self, result, code):
        self.assertTrue(result.get("isError"), result)
        self.assertEqual(result["error"], code, result)
        self.assertTrue(result["message"])


class LookupTest(ServiceTestCase):
    def test_live_lookup_is_pinned_to_the_monthly_release(self):
        result = self.service.lookup(" c3262 ")

        self.assertEqual(result["code"], "C3262")
        self.assertEqual(result["source"], "live_evs")
        self.assertEqual(result["release_version"], "26.06e")
        self.assertEqual(result["release_date"], "2026-06-29")
        self.assertNotIn("raw", result)
        self.assertNotIn("fallback", result)
        self.assertEqual(result["evidence"]["synonyms"][0]["name"], "Tumor")
        self.assertIn(("get_concept", "ncit_26.06e", "C3262"), self.evs.calls)
        self.assertEqual(self.evs.includes[-1], LOOKUP_INCLUDE)
        self.assertIn("raw", self.service.lookup("C3262", include_raw=True))

    def test_unknown_concept_is_not_found_even_when_evs_is_otherwise_healthy(self):
        self.assertError(self.service.lookup("C999"), "concept_not_found")

    def test_concept_gone_from_live_evs_is_not_served_from_the_cache(self):
        self.index()
        del self.evs.concepts["C3262"]

        self.assertError(self.service.lookup("C3262"), "concept_not_found")

    def test_cache_fallback_when_evs_is_unreachable_says_so(self):
        self.index()
        for method in ("get_concept", "resolve_monthly_ncit_release"):
            with self.subTest(failing=method):
                self.evs.errors = {method: EVSUnavailableError("connection refused")}
                with self.assertLogs("nci_si_mcp.service", level="WARNING") as logs:
                    result = self.service.lookup("C3262")

                self.assertEqual(result["source"], "active_cache")
                self.assertEqual(result["release_version"], "26.06e")
                self.assertEqual(
                    result["fallback"],
                    {"reason": "evs_unavailable", "message": "connection refused"},
                )
                self.assertNotIn("raw", result)
                self.assertIn("connection refused", logs.output[0])

    def test_no_fallback_without_a_cached_copy_or_with_live_only(self):
        self.evs.errors = {"get_concept": EVSUnavailableError("connection refused")}
        self.assertError(self.service.lookup("C3262"), "evs_unavailable")

        self.index("C40704")
        self.assertError(self.service.lookup("C3262"), "evs_unavailable")

        self.index("C3262")
        self.assertError(self.service.lookup("C3262", live_only=True), "evs_unavailable")
        self.evs.errors = {"resolve_monthly_ncit_release": EVSUnavailableError("down")}
        self.assertError(self.service.lookup("C3262", live_only=True), "evs_unavailable")

    def test_unresolved_release_fails_closed_instead_of_serving_the_cache(self):
        self.index()
        self.evs.errors = {
            "resolve_monthly_ncit_release": ReleaseResolutionError("found 2 monthly releases")
        }

        self.assertError(self.service.lookup("C3262"), "release_unresolved")

    def test_release_rollover_is_a_version_mismatch_until_live_only(self):
        self.index()
        self.evs.release = release("26.07d", "2026-07-27")
        self.evs.concepts["C3262"] = dict(NEOPLASM, version="26.07d")

        result = self.service.lookup("C3262")

        self.assertError(result, "version_mismatch")
        self.assertEqual(
            result["details"],
            {"live_release_version": "26.07d", "active_index_release_version": "26.06e"},
        )
        live = self.service.lookup("C3262", live_only=True)
        self.assertEqual((live["source"], live["release_version"]), ("live_evs", "26.07d"))

    def test_live_only_does_not_read_the_index(self):
        self.index()
        (self.path / "nci_si.sqlite3").write_bytes(b"not a database" * 100)

        self.assertError(self.service.lookup("C3262"), "index_storage_error")
        self.assertEqual(self.service.lookup("C3262", live_only=True)["source"], "live_evs")

    def test_concept_served_from_another_release_than_requested_is_rejected(self):
        self.evs.concepts["C3262"] = dict(NEOPLASM, version="26.07a")

        result = self.service.lookup("C3262", live_only=True)

        self.assertError(result, "evs_invalid_response")
        self.assertIn("26.07a", result["message"])

    def test_invalid_code_is_rejected_before_any_request(self):
        for code in ("not-a-code", "C12/children", "", None):
            with self.subTest(code=code):
                self.assertError(self.service.lookup(code), "invalid_request")
        self.assertEqual(self.evs.calls, [])

    def test_an_oversized_response_is_an_invalid_response_not_an_outage(self):
        self.index()
        self.evs.errors = {"get_concept": EVSResponseTooLargeError("too large")}

        result = self.service.lookup("C3262")

        self.assertError(result, "evs_invalid_response")
        self.assertNotIn("fallback", result)


class IndexCodesTest(ServiceTestCase):
    def test_every_returned_payload_is_validated_even_a_repeated_one(self):
        class RepeatingEVS(FakeEVS):
            def get_concepts_by_codes(self, codes, terminology="ncit", include=""):
                found = super().get_concepts_by_codes(codes, terminology, include)
                return [dict(found[0], properties="not a list"), *found]

        self.evs = RepeatingEVS([NEOPLASM])
        self.service = self.make_service()

        self.assertError(self.service.index_codes(["C3262"]), "evs_invalid_response")
        self.assertIsNone(self.service.index.get_active_manifest())

    def test_indexing_fetches_pinned_batches_and_activates_the_release(self):
        self.service = self.make_service(index_batch_size=2)
        self.evs.concepts["C2991"] = concept("C2991", "Disease or Disorder")

        result = self.service.index_codes(["c3262", "C40704", "C2991", "C3262"])

        self.assertEqual(result["concept_count"], 3)
        self.assertEqual(result["release_version"], "26.06e")
        self.assertEqual(result["release_date"], "2026-06-29")
        self.assertEqual(
            [call for call in self.evs.calls if call[0] == "get_concepts_by_codes"],
            [
                ("get_concepts_by_codes", "ncit_26.06e", ["C3262", "C40704"]),
                ("get_concepts_by_codes", "ncit_26.06e", ["C2991"]),
            ],
        )

    def test_missing_concepts_leave_the_index_unchanged(self):
        before = self.index("C3262")

        result = self.service.index_codes(["C3262", "C999"])

        self.assertError(result, "concepts_missing")
        self.assertEqual(result["details"], {"missing_codes": ["C999"]})
        self.assertEqual(self.service.index.get_active_manifest().to_dict(), before)

    def test_payload_from_another_release_than_requested_changes_nothing(self):
        before = self.index("C3262")
        self.evs.concepts["C40704"] = dict(KINASE, version="26.07a")

        for codes in (["C40704"], ["C3262", "C40704"]):
            with self.subTest(codes=codes):
                self.assertError(self.service.index_codes(codes), "evs_invalid_response")
                self.assertEqual(self.service.index.get_active_manifest().to_dict(), before)

    def test_concept_that_was_not_requested_is_an_evs_fault(self):
        class ExtraEVS(FakeEVS):
            def get_concepts_by_codes(self, codes, terminology="ncit", include=""):
                found = super().get_concepts_by_codes(codes, terminology, include)
                return [*found, concept("", "No Code")]

        self.evs = ExtraEVS([NEOPLASM])
        self.service = self.make_service()

        self.assertError(self.service.index_codes(["C3262"]), "evs_invalid_response")
        self.assertIsNone(self.service.index.get_active_manifest())

    def test_indexed_concepts_carry_their_synonyms_and_definitions(self):
        self.index("C3262")

        cached = self.service.index.get_concept("C3262")

        self.assertEqual(cached.evidence["synonyms"][0]["name"], "Tumor")
        self.assertEqual(self.evs.includes[-1], "summary,definitions,synonyms,properties")

    def test_invalid_or_empty_codes_are_rejected(self):
        for codes in ([], ["C3262", "oops"]):
            with self.subTest(codes=codes):
                self.assertError(self.service.index_codes(codes), "invalid_request")

    def test_evs_outage_is_reported(self):
        self.evs.errors = {"get_concepts_by_codes": EVSUnavailableError("timed out")}

        self.assertError(self.service.index_codes(["C3262"]), "evs_unavailable")


class SearchTest(ServiceTestCase):
    def test_search_returns_ranked_hits_of_the_indexed_release(self):
        self.index()

        result = self.service.search(" tumor ", limit=1, mode="BM25")

        self.assertEqual(result["query"], "tumor")
        self.assertEqual(result["mode"], "bm25")
        self.assertEqual(result["release_version"], "26.06e")
        self.assertEqual([hit["concept"]["code"] for hit in result["hits"]], ["C3262"])
        self.assertEqual(result["hits"][0]["rank"], 1)
        self.assertNotIn("raw", result["hits"][0]["concept"])
        with_raw = self.service.search("tumor", include_raw=True)
        self.assertIn("raw", with_raw["hits"][0]["concept"])

    def test_search_honours_limit_and_mode(self):
        self.index()

        self.assertEqual(len(self.service.search("tumor", limit=1, mode="vector")["hits"]), 1)
        self.assertEqual(len(self.service.search("tumor", mode="vector")["hits"]), 2)
        self.assertEqual(len(self.service.search("tumor", mode="bm25")["hits"]), 1)
        self.assertEqual(len(self.service.search("tumor", mode="hybrid")["hits"]), 2)

    def test_search_without_hits_still_names_the_release(self):
        self.index()

        result = self.service.search("zzzz", mode="bm25")

        self.assertEqual((result["hits"], result["release_version"]), ([], "26.06e"))

    def test_search_needs_an_index(self):
        self.assertError(self.service.search("tumor"), "no_active_index")

    def test_each_invalid_argument_is_rejected(self):
        self.index()
        for arguments in (
            {"query": " "},
            {"query": "tumor", "limit": 0},
            {"query": "tumor", "limit": 101},
            {"query": "tumor", "limit": True},
            {"query": "tumor", "mode": "fuzzy"},
        ):
            with self.subTest(arguments=arguments):
                self.assertError(self.service.search(**arguments), "invalid_request")

    def test_index_compatibility_is_reported_consistently(self):
        self.assertFalse(self.service.release_info()["embedding"]["active_index_compatible"])
        self.index()
        other = self.make_service(provider=HashingEmbeddingProvider(dimensions=64))

        self.assertError(other.search("tumor"), "index_incompatible")
        self.assertFalse(other.release_info()["embedding"]["active_index_compatible"])
        self.assertTrue(self.service.release_info()["embedding"]["active_index_compatible"])

    def test_search_reports_the_release_of_its_hits(self):
        self.index()
        with patch.object(self.service.index, "get_active_manifest", return_value=None):
            result = self.service.search("neoplasm")

        self.assertTrue(result["hits"])
        self.assertEqual(result["release_version"], "26.06e")


class TraverseTest(ServiceTestCase):
    def test_traversal_reads_the_pinned_monthly_release(self):
        result = self.service.traverse(["c3262", "C3262"], max_depth=1, edge_types=["Role"])

        self.assertEqual(result["release_version"], "26.06e")
        self.assertEqual(result["start_codes"], ["C3262"])
        self.assertEqual([node["code"] for node in result["nodes"]], ["C3262", "C12922"])
        self.assertEqual(
            [(edge["source_code"], edge["target_code"]) for edge in result["edges"]],
            [("C3262", "C12922")],
        )
        self.assertEqual(result["nodes"][0]["preferred_name"], "Neoplasm")
        self.assertIn(("get_concepts_by_codes", "ncit_26.06e", ["C3262"]), self.evs.calls)

    def test_direction_limits_and_name_filter_reach_the_walk(self):
        def targets(**arguments):
            result = self.service.traverse(["C3262"], max_depth=1, **arguments)
            return [edge["target_code"] for edge in result["edges"]], result["truncated"]

        self.assertEqual(targets(), (["C4741", "C12922", "C165258"], False))
        self.assertEqual(targets(direction="in"), (["C2991"], False))
        self.assertEqual(targets(max_edges=1), (["C4741"], True))
        self.assertEqual(targets(max_nodes=2), (["C4741"], True))
        self.assertEqual(targets(include_hierarchy=False), (["C12922", "C165258"], False))
        self.assertEqual(targets(include_roles=False), (["C4741", "C165258"], False))
        self.assertEqual(targets(include_associations=False), (["C4741", "C12922"], False))
        self.assertEqual(
            targets(relationship_names=["Disease_Has_Abnormal_Cell"]), (["C12922"], False)
        )

    def test_invalid_selection_is_rejected_before_any_request(self):
        self.evs.errors = {"resolve_monthly_ncit_release": EVSUnavailableError("down")}

        self.assertError(self.service.traverse(["C3262"], edge_types=["parent"]), "invalid_request")
        self.assertEqual(self.evs.calls, [])
        too_many = self.service.traverse(["C3262", "C40704"], max_nodes=1)
        self.assertError(too_many, "invalid_request")
        self.assertEqual(self.evs.calls, [])

    def test_failures_use_the_matching_error_code(self):
        self.assertError(self.service.traverse(["C999"]), "concept_not_found")
        self.evs.errors = {"get_concepts_by_codes": EVSUnavailableError("timed out")}
        self.assertError(self.service.traverse(["C3262"]), "evs_unavailable")
        self.evs.errors = {"resolve_monthly_ncit_release": ReleaseResolutionError("ambiguous")}
        self.assertError(self.service.traverse(["C3262"]), "release_unresolved")
        self.evs.errors = {"get_concepts_by_codes": EVSResponseError("not a list")}
        self.assertError(self.service.traverse(["C3262"]), "evs_invalid_response")

    def test_each_invalid_argument_is_rejected(self):
        for arguments in (
            {"start_codes": []},
            {"start_codes": ["oops"]},
            {"start_codes": ["C3262"], "direction": "sideways"},
            {"start_codes": ["C3262"], "max_depth": -1},
            {"start_codes": ["C3262"], "max_nodes": 0},
            {"start_codes": ["C3262"], "max_edges": 0},
            {"start_codes": ["C3262"], "edge_types": ["unknown"]},
            {"start_codes": ["C3262"], "edge_types": ["parent"]},
            {"start_codes": ["C3262"], "relationship_names": [" "]},
            {"start_codes": ["C3262", "C40704"], "max_nodes": 1},
            {
                "start_codes": ["C3262"],
                "include_hierarchy": False,
                "include_roles": False,
                "include_associations": False,
            },
        ):
            with self.subTest(arguments=arguments):
                self.assertError(self.service.traverse(**arguments), "invalid_request")


class StatusTest(ServiceTestCase):
    def test_release_info_reports_release_index_and_embedding(self):
        self.index()

        result = self.service.release_info()

        self.assertEqual(result["evs_api"], {"version": "test"})
        self.assertEqual(result["selected_monthly_release"]["version"], "26.06e")
        self.assertNotIn("raw", result["selected_monthly_release"])
        self.assertEqual(result["active_index"]["concept_count"], 2)
        self.assertEqual(result["embedding"]["provider"], "hashing")

    def test_release_info_survives_an_evs_outage(self):
        self.index("C3262")
        self.evs.errors = {
            "get_api_version": EVSUnavailableError("down"),
            "resolve_monthly_ncit_release": ReleaseResolutionError("found 2 monthly releases"),
        }

        result = self.service.release_info()

        self.assertNotIn("isError", result)
        self.assertError(result["evs_api"], "evs_unavailable")
        self.assertError(result["selected_monthly_release"], "release_unresolved")
        self.assertIn("found 2", result["selected_monthly_release"]["message"])
        self.assertEqual(result["active_index"]["release_version"], "26.06e")

    def test_evaluate_scores_every_mode_and_names_gold_concepts_that_are_not_indexed(self):
        self.assertError(self.service.evaluate(), "no_active_index")
        self.index()

        result = self.service.evaluate()

        self.assertEqual([item["mode"] for item in result["results"]], ["bm25", "vector", "hybrid"])
        self.assertEqual(result["gold_codes_not_indexed"], ["C116938"])

    def test_index_manifest_is_null_until_an_index_exists(self):
        self.assertEqual(self.service.index_manifest(), {"active_index": None})
        self.index()

        self.assertEqual(self.service.index_manifest()["active_index"]["concept_count"], 2)

    def test_settings_configure_the_evs_client(self):
        service = NCISIService(
            Settings(
                data_dir=self.path,
                evs_base_url="http://localhost:8080",
                timeout_seconds=2.5,
                evs_max_attempts=7,
                evs_retry_backoff_seconds=1.5,
                evs_max_response_bytes=123,
            )
        )

        self.assertIsInstance(service.evs, EVSClient)
        self.assertEqual(
            (
                service.evs.base_url,
                service.evs.timeout_seconds,
                service.evs.max_attempts,
                service.evs.retry_backoff_seconds,
                service.evs.max_response_bytes,
            ),
            ("http://localhost:8080", 2.5, 7, 1.5, 123),
        )
        self.assertEqual(service.embedding_provider.model, "hashing-128")

    def test_cadsr_reports_that_reuse_is_pending(self):
        status = self.service.cadsr_status()

        self.assertEqual(status["state"], "reuse_pending")
        self.assertEqual(status["findings"], [])


class FailureHandlingTest(ServiceTestCase):
    def test_unusable_database_is_reported_with_its_path(self):
        self.index()
        database = self.path / "nci_si.sqlite3"
        database.write_bytes(b"not a database" * 100)
        calls = {
            "index_codes": lambda: self.service.index_codes(["C3262"]),
            "search": lambda: self.service.search("tumor"),
            "lookup": lambda: self.service.lookup("C3262"),
            "release_info": self.service.release_info,
            "index_manifest": self.service.index_manifest,
            "evaluate": self.service.evaluate,
        }
        for operation, call in calls.items():
            with self.subTest(operation):
                with self.assertLogs("nci_si_mcp.service", level="WARNING"):
                    result = call()
                self.assertError(result, "index_storage_error")
                self.assertIn(str(database), result["message"])

    def test_misuse_of_the_index_is_not_disguised_as_a_result(self):
        with self.assertRaises(IndexBuildError):
            self.service.index.upsert_concepts([], None, HashingEmbeddingProvider())
        self.assertNotIn(IndexBuildError, service_module._ERROR_CODES)

    def test_unexpected_exceptions_are_not_disguised_as_results(self):
        class BrokenProvider(HashingEmbeddingProvider):
            def embed(self, texts):
                return int("not-a-number")

        service = self.make_service(provider=BrokenProvider())

        with self.assertRaises(ValueError):
            service.index_codes(["C3262"])


if __name__ == "__main__":
    unittest.main()
