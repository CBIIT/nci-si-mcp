"""The provenance of every item a tool returns, and the truncation record of a bounded result.

The fields come from spec/records.yaml (`provenance`, `traversal`, `truncation`); the tests read
the specification, so a field added or renamed there fails here.
"""

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

import yaml

from fakes import FakeEVS, concept, release
from nci_si_mcp.config import Settings
from nci_si_mcp.embeddings import HashingEmbeddingProvider
from nci_si_mcp.errors import call_correlation_id, correlated
from nci_si_mcp.evs import EVSResponseTooLargeError, EVSUnavailableError
from nci_si_mcp.index import LocalIndex
from nci_si_mcp.models import NcitConcept
from nci_si_mcp.service import NCISIService
from nci_si_mcp.traversal import NCIT_EXCLUSION_CODES

RECORDS = yaml.safe_load((Path(__file__).parent.parent / "spec/records.yaml").read_text())
PROVENANCE = RECORDS["provenance"]["fields"]
TRAVERSAL = RECORDS["traversal"]["fields"]
TRUNCATION = RECORDS["truncation"]["fields"]
# Fields the record lists but a server supplies only where its source does: graphs belong to
# items the Shared SI Service serves, upstream is what the platform supplied, and qualifiers and
# evidence are what the platform attaches to a relation.
CONDITIONAL = {"graphs", "upstream", "qualifiers", "evidence"}
REQUIRED = {name for name, spec in PROVENANCE.items() if not spec.get("optional")} - CONDITIONAL
REQUIRED_TRAVERSAL = {name for name in TRAVERSAL if name not in CONDITIONAL}
ALLOWED = set(PROVENANCE)
ALLOWED_TRAVERSAL = ALLOWED | set(TRAVERSAL)


def related(name, code, relationship_code="R1"):
    return {
        "code": relationship_code,
        "type": name,
        "relatedCode": code,
        "relatedName": f"Concept {code}",
    }


def child(code):
    return {"code": code, "name": f"Concept {code}"}


NEOPLASM = concept(
    "C3262",
    "Neoplasm",
    parents=[child("C2991")],
    children=[child("C4741"), child("C4742")],
    roles=[related("Disease_Has_Abnormal_Cell", "C12922", "R100")],
    inverseRoles=[related("Gene_Associated_With_Disease", "C16612", "R101")],
    associations=[related("Concept_In_Subset", "C165258", "A8")],
)
KINASE = concept("C40704", "Receptor Tyrosine Kinase Inhibition")


class ProvenanceTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        # The tests change the payloads, so each gets its own.
        payloads = [NEOPLASM, KINASE, *(concept(code) for code in ("C2991", "C4741", "C4742"))]
        self.evs = FakeEVS(deepcopy(payloads))
        self.service = NCISIService(
            Settings(data_dir=self.path),
            evs=self.evs,
            index=LocalIndex(self.path),
            embedding_provider=HashingEmbeddingProvider(),
        )

    def assert_record(self, provenance, required, allowed):
        """The provenance holds exactly the record's fields: all it requires, none beyond."""

        self.assertLessEqual(required, set(provenance), provenance)
        self.assertLessEqual(set(provenance), allowed, provenance)

    def traversal(self, **arguments):
        arguments = {"start_codes": ["C3262"], "max_depth": 1} | arguments
        result = self.service.traverse(**arguments)
        self.assertNotIn("error", result, result)
        return result

    def edge(self, result, target):
        (edge,) = [edge for edge in result["edges"] if edge["target_code"] == target]
        return edge


class EveryItemCarriesItsProvenanceTest(ProvenanceTestCase):
    def test_a_looked_up_concept_carries_the_provenance_record(self):
        provenance = self.service.lookup("C3262")["provenance"]

        self.assert_record(provenance, REQUIRED, ALLOWED)
        self.assertEqual(
            provenance["release"],
            {"terminology": "ncit", "identifier": "26.06e", "date": "2026-06-29"},
        )
        self.assertEqual((provenance["source"], provenance["servedBy"]), ("evs_rest", "live"))
        self.assertEqual(
            provenance["sourceUri"], "https://evs.test/api/v1/concept/ncit_26.06e/C3262"
        )
        self.assertRegex(provenance["retrievedAt"], r"^\d{4}-\d\d-\d\dT[\d:.]+Z$")

    def test_every_source_and_way_of_serving_is_a_value_of_the_record(self):
        self.service.index_codes(["C3262"])
        live = self.service.lookup("C3262")["provenance"]
        indexed = self.service.search("neoplasm")["hits"][0]["concept"]["provenance"]
        nodes = self.traversal()["nodes"]

        for provenance in (live, indexed, nodes[0]["provenance"]):
            self.assertIn(provenance["source"], PROVENANCE["source"]["values"])
            self.assertIn(provenance["servedBy"], PROVENANCE["servedBy"]["values"])

    def test_every_node_and_edge_of_a_traversal_carries_the_traversal_record(self):
        result = self.traversal(direction="both")

        for item in [*result["nodes"], *result["edges"]]:
            provenance = item["provenance"]
            required = REQUIRED | REQUIRED_TRAVERSAL
            # The concept asked about is reached by no relationship.
            if provenance["depth"] == 0:
                required -= {"relationship", "direction", "polarity"}
            self.assert_record(provenance, required, ALLOWED_TRAVERSAL)
            self.assertIn(provenance.get("polarity", "positive"), TRAVERSAL["polarity"]["values"])

    def test_a_search_hit_carries_the_record_and_a_result_with_hits_does_not(self):
        self.service.index_codes(["C3262"])

        result = self.service.search("neoplasm")

        self.assert_record(result["hits"][0]["concept"]["provenance"], REQUIRED, ALLOWED)
        self.assertNotIn("provenance", result)

    def test_a_search_without_hits_carries_the_provenance_itself(self):
        self.service.index_codes(["C3262"])

        result = self.service.search("zzzz", mode="bm25")

        self.assertEqual(result["hits"], [])
        # Nothing was produced from an upstream URL, so there is none to name.
        self.assert_record(result["provenance"], REQUIRED - {"sourceUri"}, ALLOWED)
        self.assertEqual(result["provenance"]["servedBy"], "index")

    def test_the_release_report_and_the_index_manifest_carry_it(self):
        self.service.index_codes(["C3262"])

        report = self.service.release_info()["provenance"]
        manifest = self.service.index_manifest()["active_index"]["provenance"]

        self.assert_record(report, REQUIRED, ALLOWED)
        self.assertEqual(report["release"]["identifier"], "26.06e")
        self.assertEqual(report["sourceUri"], "https://evs.test/api/v1/metadata/terminologies")
        self.assert_record(manifest, REQUIRED - {"sourceUri"}, ALLOWED)
        self.assertEqual((manifest["source"], manifest["servedBy"]), ("evs_index", "index"))

    def test_the_release_report_names_no_release_when_evs_selected_none(self):
        self.evs.errors = {"resolve_monthly_ncit_release": EVSUnavailableError("down")}

        report = self.service.release_info()

        self.assertEqual(
            report["selected_monthly_release"]["error"]["code"], "upstream_unavailable"
        )
        self.assertNotIn("provenance", report)


class TheReleaseOfAnItemIsTheReleaseServedTest(ProvenanceTestCase):
    def test_every_item_names_the_release_the_call_was_pinned_to(self):
        self.evs.release = release("26.07d", "2026-07-27")
        for code in ("C3262", "C4741", "C2991", "C4742"):
            self.evs.concepts[code] = dict(
                self.evs.concepts.get(code, concept(code)), version="26.07d"
            )
        self.evs.concepts["C3262"]["children"] = [child("C4741"), child("C4742")]

        items = [self.service.lookup("C3262")]
        result = self.traversal()
        items += [*result["nodes"], *result["edges"]]

        self.assertEqual(
            {item["provenance"]["release"]["identifier"] for item in items}, {"26.07d"}
        )
        self.assertEqual({item["provenance"]["release"]["date"] for item in items}, {"2026-07-27"})

    def test_an_indexed_concept_names_the_release_it_was_indexed_from(self):
        self.service.index_codes(["C3262"])
        self.evs.release = release("26.07d", "2026-07-27")

        hit = self.service.search("neoplasm")["hits"][0]

        self.assertEqual(hit["concept"]["provenance"]["release"]["identifier"], "26.06e")
        self.assertEqual(
            hit["concept"]["provenance"]["sourceUri"],
            "https://evs.test/api/v1/concept/ncit_26.06e/C3262",
        )


class CorrelationTest(ProvenanceTestCase):
    def test_every_item_of_a_call_carries_the_calls_identifier(self):
        with correlated("call-42"):
            result = self.traversal(direction="both")
            lookup = self.service.lookup("C3262")

        items = [lookup, *result["nodes"], *result["edges"]]
        self.assertEqual({item["provenance"]["correlationId"] for item in items}, {"call-42"})

    def test_a_call_outside_any_adapter_is_one_call_with_one_identifier(self):
        result = self.traversal(direction="both")

        identifiers = {item["provenance"]["correlationId"] for item in result["nodes"]}
        identifiers |= {item["provenance"]["correlationId"] for item in result["edges"]}
        self.assertEqual(len(identifiers), 1)
        self.assertTrue(identifiers.pop())

    def test_an_item_cannot_be_built_outside_any_call(self):
        with self.assertRaises(RuntimeError):
            call_correlation_id()


class AnItemReachedByTraversalSaysHowTest(ProvenanceTestCase):
    def test_the_concept_asked_about_has_depth_zero_and_no_relationship(self):
        start = self.traversal()["nodes"][0]["provenance"]

        self.assertEqual(start["depth"], 0)
        for name in ("relationship", "direction", "polarity"):
            self.assertNotIn(name, start)

    def test_a_role_names_its_relationship_by_code_and_name(self):
        edge = self.edge(self.traversal(edge_types=["role"]), "C12922")["provenance"]

        self.assertEqual(
            (edge["depth"], edge["direction"], edge["polarity"]), (1, "out", "positive")
        )
        self.assertEqual(
            edge["relationship"],
            {"kind": "role", "code": "R100", "name": "Disease_Has_Abnormal_Cell"},
        )

    def test_an_inverse_association_is_followed_inward_and_an_association_outward(self):
        self.evs.concepts["C3262"]["inverseAssociations"] = [related("Has_Value", "C777", "A9")]

        result = self.traversal(direction="both", edge_types=["association", "inverse_association"])

        by_target = {edge["target_code"]: edge["provenance"] for edge in result["edges"]}
        self.assertEqual(by_target["C165258"]["direction"], "out")
        self.assertEqual(by_target["C777"]["direction"], "in")
        self.assertEqual(by_target["C777"]["relationship"]["kind"], "association")

    def test_a_hierarchy_link_has_a_kind_and_no_invented_code_or_name(self):
        result = self.traversal(direction="both", edge_types=["child", "parent"])

        child_edge = self.edge(result, "C4741")["provenance"]
        parent_edge = self.edge(result, "C2991")["provenance"]
        self.assertEqual(child_edge["relationship"], {"kind": "child"})
        self.assertEqual(parent_edge["relationship"], {"kind": "parent"})
        self.assertEqual((child_edge["direction"], parent_edge["direction"]), ("out", "in"))
        self.assertEqual(child_edge["polarity"], "positive")

    def test_a_node_carries_the_edge_that_reached_it_and_its_own_uri(self):
        result = self.traversal(edge_types=["role"])

        (node,) = [n["provenance"] for n in result["nodes"] if n["code"] == "C12922"]
        edge = self.edge(result, "C12922")["provenance"]

        self.assertEqual(
            {key: node[key] for key in ("depth", "relationship", "direction", "polarity")},
            {key: edge[key] for key in ("depth", "relationship", "direction", "polarity")},
        )
        self.assertEqual(node["sourceUri"], "https://evs.test/api/v1/concept/ncit_26.06e/C12922")
        # The relation was read from the concept that holds it.
        self.assertEqual(edge["sourceUri"], "https://evs.test/api/v1/concept/ncit_26.06e/C3262")

    def test_an_edge_has_the_depth_of_the_node_it_reaches(self):
        self.evs.concepts["C4741"]["children"] = [child("C9")]
        self.evs.concepts["C9"] = concept("C9")

        result = self.traversal(max_depth=2, edge_types=["child"])

        self.assertEqual(
            {(edge["target_code"], edge["provenance"]["depth"]) for edge in result["edges"]},
            {("C4741", 1), ("C4742", 1), ("C9", 2)},
        )
        nodes = {node["code"]: node["provenance"]["depth"] for node in result["nodes"]}
        self.assertEqual(nodes, {"C3262": 0, "C4741": 1, "C4742": 1, "C9": 2})

    def test_a_descendant_edge_is_read_from_the_descendants_of_its_start_code(self):
        self.evs.descendants = {"C3262": [{"code": "C4741", "name": "N", "level": 1}]}

        edge = self.edge(self.traversal(edge_types=["descendant"]), "C4741")["provenance"]

        self.assertEqual(edge["relationship"], {"kind": "descendant"})
        self.assertTrue(edge["sourceUri"].endswith("/ncit_26.06e/C3262/descendants"))

    def test_polarity_follows_the_relationship_code_never_its_name(self):
        exclusion = related("Disease_Excludes_Finding", "C500", "R135")
        lookalike = related("Disease_Excludes_Finding", "C501", "R999")
        self.evs.concepts["C3262"]["roles"] = [exclusion, lookalike]

        result = self.traversal(edge_types=["role"])

        self.assertEqual(self.edge(result, "C500")["provenance"]["polarity"], "negative")
        self.assertEqual(self.edge(result, "C501")["provenance"]["polarity"], "positive")

    def test_the_exclusion_codes_are_the_specifications(self):
        self.assertEqual(NCIT_EXCLUSION_CODES, set(TRAVERSAL["polarity"]["exclusions"]["ncit"]))


class UpstreamPassThroughTest(ProvenanceTestCase):
    def test_what_evs_says_of_a_concepts_origin_is_passed_through_unchanged(self):
        provenance = self.service.lookup("C3262")["provenance"]

        self.assertEqual(provenance["upstream"], {"terminology": "ncit", "version": "26.06e"})

    def test_an_indexed_concept_passes_through_what_the_index_stored(self):
        self.service.index_codes(["C3262"])

        provenance = self.service.search("neoplasm")["hits"][0]["concept"]["provenance"]

        self.assertEqual(provenance["upstream"], {"terminology": "ncit", "version": "26.06e"})

    def test_upstream_is_absent_where_the_platform_supplied_nothing(self):
        bare = NcitConcept(
            code="C1",
            preferred_name="One",
            source_vocabulary="NCI Thesaurus",
            terminology="ncit",
            release_version="26.06e",
            release_date=None,
            retrieved_at="2026-07-09T00:00:00Z",
            source="live_evs",
            raw={"code": "C1", "name": "One", "unrelated": "x"},
        )

        with correlated("call-1"):
            provenance = bare.to_dict("https://evs.test/c1")["provenance"]

        self.assertNotIn("upstream", provenance)
        # A release EVS gave no date for is named without one, never with null.
        self.assertEqual(provenance["release"], {"terminology": "ncit", "identifier": "26.06e"})

    def test_a_traversal_node_names_no_upstream_origin_the_relation_did_not_give(self):
        for item in self.traversal()["nodes"]:
            self.assertNotIn("upstream", item["provenance"])


class RawIsKeptBehindTheFlagTest(ProvenanceTestCase):
    def test_a_result_carries_no_raw_payload_unless_asked_for(self):
        self.service.index_codes(["C3262"])

        self.assertNotIn("raw", self.service.lookup("C3262"))
        self.assertIn("raw", self.service.lookup("C3262", include_raw=True))
        self.assertNotIn("raw", self.service.search("neoplasm")["hits"][0]["concept"])
        self.assertIn(
            "raw", self.service.search("neoplasm", include_raw=True)["hits"][0]["concept"]
        )


class TruncationTest(ProvenanceTestCase):
    def assert_record(self, truncation, expected):
        self.assertEqual(truncation, expected)
        self.assertLessEqual(set(truncation), set(TRUNCATION))
        if truncation["occurred"]:
            self.assertIsInstance(truncation["omitted"], int)
            self.assertIn(truncation["bound"], TRUNCATION["bound"]["values"])
            self.assertIsInstance(truncation["exact"], bool)

    def test_a_walk_no_bound_stopped_holds_occurred_false_and_nothing_else(self):
        self.assert_record(self.traversal()["truncation"], {"occurred": False})

    def test_the_node_limit_reports_the_concepts_it_dropped(self):
        truncation = self.traversal(edge_types=["child"], max_nodes=2)["truncation"]

        # Two of the three nodes fit: the start code and one child; one child was dropped.
        self.assert_record(
            truncation,
            {
                "occurred": True,
                "bound": "nodes",
                "limit": 2,
                "reached": 2,
                "omitted": 1,
                "exact": False,
            },
        )

    def test_the_edge_limit_reports_the_edges_it_dropped(self):
        truncation = self.traversal(edge_types=["child", "role"], max_edges=1)["truncation"]

        self.assert_record(
            truncation,
            {
                "occurred": True,
                "bound": "edges",
                "limit": 1,
                "reached": 1,
                "omitted": 2,
                "exact": False,
            },
        )

    def test_an_edge_to_a_node_the_node_limit_dropped_counts_for_the_node_bound(self):
        truncation = self.traversal(edge_types=["child", "role"], max_nodes=1, max_edges=1)

        self.assertEqual(truncation["truncation"]["bound"], "nodes")
        self.assertEqual(truncation["edges"], [])

    def test_the_first_bound_reached_is_the_one_reported(self):
        # A parallel edge to the child kept is dropped by the edge limit before the node limit
        # drops the role's new target: the edge limit is the bound reported.
        self.evs.concepts["C3262"]["children"] = [child("C4741")]
        self.evs.concepts["C3262"]["roles"] = [
            related("Is_Parallel_To", "C4741", "R50"),
            related("Has_Other", "C12922", "R51"),
        ]

        result = self.traversal(edge_types=["child", "role"], max_nodes=2, max_edges=1)

        self.assertEqual(result["truncation"]["bound"], "edges")
        self.assertEqual(result["truncation"]["omitted"], 1)

    def test_a_concept_too_large_to_read_is_reported_against_the_upstream_cap(self):
        class Hub(FakeEVS):
            def get_concepts_by_codes(self, codes, terminology="ncit", include=""):
                if include != "minimal":
                    raise EVSResponseTooLargeError("too large")
                return super().get_concepts_by_codes(codes, terminology, include)

        self.service.evs = Hub([NEOPLASM])
        with self.assertLogs("nci_si_mcp.traversal", level="WARNING"):
            truncation = self.traversal(edge_types=["child"])["truncation"]

        self.assert_record(
            truncation,
            {
                "occurred": True,
                "bound": "upstream_cap",
                "limit": 1_000_000,
                "reached": 1_000_000,
                "omitted": 1,
                "exact": False,
            },
        )

    def test_stopping_at_the_depth_limit_is_no_truncation(self):
        self.assert_record(self.traversal(max_depth=0)["truncation"], {"occurred": False})

    def test_the_search_limit_reports_the_concepts_it_left_out(self):
        self.service.index_codes(["C3262", "C40704"])

        result = self.service.search("tumor", limit=1, mode="vector")

        # Every concept of a small index is scored, so the count is exact.
        self.assert_record(
            result["truncation"],
            {
                "occurred": True,
                "bound": "results",
                "limit": 1,
                "reached": 1,
                "omitted": 1,
                "exact": True,
            },
        )
        self.assert_record(
            self.service.search("tumor", mode="vector")["truncation"], {"occurred": False}
        )
