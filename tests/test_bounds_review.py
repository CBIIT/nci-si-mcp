import unittest
from unittest.mock import patch

from fakes import FakeEVS, concept
from nci_si_mcp.errors import correlated
from nci_si_mcp.evs import EVSNotFoundError
from test_bounds import BudgetEVS, BudgetHub
from test_traversal import child, codes, complete_graph, descendant, related, walk


def roles(*targets):
    return [related(f"r{n}", code) for n, code in enumerate(targets)]


class BoundsReviewTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(correlated("bounds-review"))

    def test_node_bound_precedes_later_request_exhaustion(self):
        client = BudgetEVS([concept("C1", children=[child("C2"), child("C3"), child("C4")])])
        result = walk(client, requests=1, max_nodes=3, max_depth=2, edge_types=["child"])
        self.assertEqual(result.truncation.bound, "nodes")

    def test_global_node_bound_precedes_a_simultaneous_kind_bound(self):
        result = walk(
            complete_graph(FakeEVS([concept("C1", roles=roles("C2", "C3", "C4"))])),
            max_depth=1,
            max_nodes=3,
            budget_per_kind=2,
        )
        self.assertEqual(result.truncation.bound, "nodes")

    def test_kind_exhaustion_leaves_other_kinds_free_to_expand_at_the_next_depth(self):
        client = FakeEVS(
            [
                concept("C1", roles=roles("C2", "C3")),
                concept("C2", associations=[related("a", "C5")]),
            ]
        )
        result = walk(complete_graph(client), budget_per_kind=1, max_depth=2)
        self.assertIn("C5", codes(result))

    def test_request_exhaustion_stops_before_later_descendant_levels(self):
        client = BudgetEVS(
            [concept("C1", children=[child("C2")])],
            descendants={"C1": [descendant("C2", 1), descendant("C3", 2), descendant("C4", 3)]},
        )
        result = walk(client, requests=2, max_depth=3, edge_types=["child", "descendant"])
        self.assertNotIn("C4", codes(result))
        self.assertEqual(result.truncation.to_dict()["perKind"]["descendant"]["bound"], "requests")

    def test_per_kind_edge_omissions_count_parallel_edges(self):
        result = walk(
            FakeEVS([concept("C1", roles=roles("C2", "C2", "C2"))]), max_depth=1, max_edges=1
        )
        self.assertEqual(result.truncation.to_dict()["perKind"]["role"]["omitted"], 2)

    def test_top_kind_omission_does_not_include_another_kinds_nodes(self):
        client = FakeEVS(
            [
                concept(
                    "C1",
                    roles=roles("C2", "C3"),
                    associations=[related("a1", "C4"), related("a2", "C5")],
                )
            ]
        )
        result = walk(complete_graph(client), max_depth=1, budget_per_kind=1)
        self.assertEqual(result.truncation.omitted, 1)

    def test_per_kind_first_bound_survives_later_global_node_exhaustion(self):
        client = FakeEVS(
            [
                concept("C1", roles=roles("C2", "C3")),
                concept("C2", children=[child("C4")], roles=roles("C5")),
            ]
        )
        result = walk(complete_graph(client), max_depth=2, max_nodes=3, budget_per_kind=1)
        self.assertEqual(result.truncation.to_dict()["perKind"]["role"]["bound"], "kind_budget")

    def test_reconciliation_refreshes_the_first_kind(self):
        client = FakeEVS(
            [
                concept(
                    "C1",
                    roles=roles("C2", "C3"),
                    associations=[related("a1", "C1"), related("a2", "C3"), related("a3", "C5")],
                )
            ]
        )
        result = walk(complete_graph(client), budget_per_kind=1, max_depth=1)
        self.assertEqual(result.truncation.omitted, 1)

    def test_reconciliation_removes_a_now_present_node_from_omissions(self):
        client = FakeEVS(
            [
                concept(
                    "C1",
                    roles=roles("C2", "C3", "C4"),
                    associations=[related("a1", "C1"), related("a2", "C3")],
                )
            ]
        )
        result = walk(complete_graph(client), max_nodes=3, budget_per_kind=1, max_depth=1)
        self.assertEqual((result.truncation.bound, result.truncation.omitted), ("nodes", 1))

    def test_upstream_cap_precedes_later_node_exhaustion(self):
        client = BudgetHub(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2"),
                concept("C3", children=[child("C4"), child("C5")]),
            ]
        )
        result = walk(complete_graph(client), max_nodes=4, max_depth=2)
        self.assertEqual(result.truncation.bound, "upstream_cap")

    def test_per_kind_node_reached_counts_starts_and_not_edges(self):
        result = walk(
            complete_graph(FakeEVS([concept("C1", roles=roles("C2", "C3", "C4"))])),
            max_depth=1,
            max_nodes=3,
        )
        self.assertEqual(result.truncation.to_dict()["perKind"]["role"]["reached"], 3)

    def test_missing_minimal_fallback_still_reports_not_found(self):
        with self.assertRaises(EVSNotFoundError):
            walk(BudgetHub(), start_codes=["C2"])

    def test_reconciled_drops_do_not_hide_an_upstream_cap(self):
        client = BudgetHub(
            [
                concept("C1", roles=roles("C2", "C3"), associations=[related("a", "C4")]),
                concept("C2"),
                concept("C4", children=[child("C3")]),
            ]
        )
        result = walk(complete_graph(client), budget_per_kind=1, max_depth=2)
        self.assertEqual(result.truncation.bound, "upstream_cap")

    def test_an_oversized_split_precedes_a_later_request_cap_in_the_same_fetch(self):
        client = BudgetHub([concept("C2"), concept("C1")])
        result = walk(client, start_codes=["C2", "C1"], requests=3)
        self.assertEqual(result.truncation.bound, "upstream_cap")
        self.assertEqual(result.truncation.to_dict()["perKind"]["role"]["bound"], "upstream_cap")

    def test_surviving_later_drop_does_not_hide_an_intervening_upstream_cap(self):
        client = BudgetHub(
            [
                concept("C1", roles=roles("C2", "C3"), associations=[related("a", "C4")]),
                concept("C2"),
                concept("C4", children=[child("C3"), child("C5")]),
            ]
        )
        result = walk(complete_graph(client), budget_per_kind=1, max_depth=2)
        self.assertEqual(result.truncation.bound, "upstream_cap")
        self.assertEqual(result.truncation.to_dict()["perKind"]["child"]["bound"], "upstream_cap")

    def test_unread_kind_bound_precedes_its_later_known_node_drop(self):
        client = BudgetHub(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2"),
                concept("C3", roles=roles("C4", "C5")),
            ]
        )
        result = walk(complete_graph(client), max_nodes=4, max_depth=2)
        self.assertEqual(result.truncation.to_dict()["perKind"]["role"]["bound"], "upstream_cap")

    def test_reconciled_target_still_reports_an_edge_cap(self):
        client = FakeEVS(
            [
                concept(
                    "C1",
                    roles=roles("C2", "C3"),
                    associations=[related("a1", "C2"), related("a2", "C3")],
                )
            ]
        )
        result = walk(client, max_depth=1, budget_per_kind=1, max_edges=3)
        expected = {
            "occurred": True,
            "bound": "edges",
            "limit": 3,
            "reached": 3,
            "omitted": 1,
            "exact": False,
        }
        record = result.truncation.to_dict()
        self.assertEqual(record.pop("perKind")["role"], expected)
        self.assertEqual(record, expected)
        self.assertEqual(codes(result), ["C1", "C2", "C3"])
        self.assertEqual(len(result.edges), 3)

    def test_duplicate_omissions_retain_their_original_bound(self):
        client = FakeEVS(
            [
                concept(
                    "C1",
                    roles=[related("r1", "C2"), related("r2", "C3"), related("r2", "C3")],
                    associations=[related("a1", "C1"), related("a2", "C4")],
                )
            ]
        )
        result = walk(complete_graph(client), max_depth=1, max_nodes=3, budget_per_kind=1)
        self.assertEqual(result.truncation.bound, "kind_budget")
        self.assertEqual(result.truncation.to_dict()["perKind"]["role"]["bound"], "kind_budget")

    def test_oversized_relations_precede_a_blocked_minimal_fallback(self):
        client = BudgetHub([concept("C1", children=[child("C2")]), concept("C2")])
        result = walk(client, requests=2, max_depth=2)
        self.assertEqual(result.truncation.bound, "upstream_cap")
        self.assertEqual(result.truncation.to_dict()["perKind"]["child"]["bound"], "upstream_cap")

    def test_edge_stop_reports_the_next_frontiers_unread_kinds(self):
        client = FakeEVS(
            [
                concept("C1", children=[child("C2")], roles=roles("C3")),
                concept("C2", children=[child("C4")]),
            ]
        )
        result = walk(client, max_depth=2, max_edges=1, edge_types=["child", "role"])
        self.assertEqual(
            result.truncation.to_dict()["perKind"]["child"],
            {
                "occurred": True,
                "bound": "edges",
                "limit": 1,
                "reached": 1,
                "omitted": 0,
                "exact": False,
            },
        )

    def test_upstream_cap_counts_concepts_but_leaves_kind_omissions_unknown(self):
        result = walk(
            BudgetHub([concept("C2")]),
            start_codes=["C2"],
            max_depth=1,
            edge_types=["child", "role"],
        )
        self.assertEqual(result.truncation.bound, "upstream_cap")
        self.assertEqual(result.truncation.omitted, 1)
        self.assertFalse(result.truncation.exact)
        for kind in ["child", "role"]:
            with self.subTest(kind=kind):
                record = result.truncation.to_dict()["perKind"][kind]
                self.assertEqual(record["bound"], "upstream_cap")
                self.assertEqual(record["omitted"], 0)
                self.assertIs(record["exact"], False)

    def test_edge_stop_prevents_final_checks_for_other_kinds(self):
        result = walk(
            FakeEVS([concept("C1", children=[child("C2")], roles=roles("C3"))]),
            max_depth=1,
            max_edges=1,
            edge_types=["child", "role"],
        )
        records = result.truncation.to_dict()["perKind"]
        self.assertEqual(records["child"]["bound"], "edges")
        self.assertEqual(records["child"]["omitted"], 0)
        self.assertFalse(records["child"]["exact"])
        self.assertEqual(records["role"]["bound"], "edges")
        self.assertEqual(codes(result), ["C1", "C2"])

    def test_edge_stop_without_new_nodes_leaves_undropped_kinds_complete(self):
        result = walk(
            FakeEVS([concept("C1", children=[child("C1")], roles=roles("C1"))]),
            max_depth=2,
            max_edges=1,
            edge_types=["child", "role"],
        )
        records = result.truncation.to_dict()["perKind"]
        self.assertEqual(records["child"], {"occurred": False})
        self.assertEqual(records["role"]["bound"], "edges")
        self.assertEqual(codes(result), ["C1"])

    def test_partial_starts_report_requests_without_any_relation_reads(self):
        with patch("nci_si_mcp.traversal.BATCH_SIZE", 1):
            result = walk(
                BudgetEVS([concept("C1"), concept("C2")]),
                start_codes=["C1", "C2"],
                max_depth=0,
                requests=1,
                edge_types=["descendant"],
            )
        self.assertEqual(codes(result), ["C1"])
        self.assertEqual(
            result.truncation.to_dict(),
            {
                "occurred": True,
                "bound": "requests",
                "limit": 1,
                "reached": 1,
                "omitted": 1,
                "exact": False,
            },
        )
