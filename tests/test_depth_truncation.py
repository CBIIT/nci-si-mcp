import unittest
from unittest.mock import patch

from fakes import FakeEVS, concept
from nci_si_mcp.errors import correlated
from nci_si_mcp.evs import EVSResponseError
from test_bounds import BudgetEVS, BudgetHub
from test_traversal import child, codes, related, walk


class DepthTruncationTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(correlated("depth-test"))

    def test_leaf_and_cycle_to_an_existing_node_are_complete(self):
        for relations in ([], [child("C1")], [child("C2")]):
            with self.subTest(relations=relations):
                client = FakeEVS(
                    [
                        concept("C1", children=[child("C2")]),
                        concept("C2", children=relations),
                    ]
                )
                result = walk(client, max_depth=1, edge_types=["child"])
                self.assertEqual(result.truncation.to_dict(), {"occurred": False})
                self.assertEqual(codes(result), ["C1", "C2"])
                self.assertEqual(client.includes, ["minimal,children", "minimal,children"])

    def test_depth_counts_distinct_unseen_targets_and_preserves_kind_counts(self):
        client = FakeEVS(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2", children=[child("C4"), child("C4")], roles=[related("r", "C4")]),
                concept("C3", children=[child("C4"), child("C5"), child("C1")]),
            ]
        )
        result = walk(client, max_depth=1, edge_types=["child", "role"])
        cut = result.truncation.to_dict()
        self.assertEqual(
            {k: cut[k] for k in ("bound", "limit", "reached", "omitted", "exact")},
            {"bound": "depth", "limit": 1, "reached": 1, "omitted": 2, "exact": False},
        )
        self.assertEqual(cut["perKind"]["child"]["omitted"], 2)
        self.assertEqual(cut["perKind"]["role"]["omitted"], 1)
        self.assertEqual(codes(result), ["C1", "C2", "C3"])

    def test_check_only_follows_selected_kinds_and_relationship_names(self):
        client = FakeEVS(
            [
                concept("C1", roles=[related("kept", "C2")]),
                concept("C2", roles=[related("ignored", "C3")], children=[child("C4")]),
            ]
        )
        result = walk(client, max_depth=1, edge_types=["role"], relationship_names=["kept"])
        self.assertFalse(result.truncation.occurred)
        self.assertEqual(client.includes, ["minimal,roles", "minimal,roles"])

    def test_clamped_depth_names_the_applied_limit_and_does_not_emit_the_next_level(self):
        client = FakeEVS([concept(f"C{i}", children=[child(f"C{i + 1}")]) for i in range(1, 7)])
        result = walk(client, max_depth=99, edge_types=["child"])
        self.assertEqual((result.truncation.bound, result.truncation.limit), ("depth", 4))
        self.assertEqual((result.truncation.reached, result.truncation.omitted), (4, 1))
        self.assertEqual(codes(result), ["C1", "C2", "C3", "C4", "C5"])

    def test_request_bound_on_the_check_is_reported_without_guessing_continuation(self):
        client = BudgetEVS(
            [
                concept("C1", children=[child("C2")]),
                concept("C2", children=[child("C3")]),
            ]
        )
        result = walk(client, max_depth=1, edge_types=["child"], requests=1)
        self.assertEqual((result.truncation.bound, result.truncation.reached), ("requests", 1))
        self.assertFalse(result.truncation.exact)
        self.assertEqual(len(client.calls), 1)

    def test_depth_found_in_an_earlier_batch_wins_over_later_request_exhaustion(self):
        client = BudgetEVS(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2", children=[child("C4")]),
                concept("C3"),
            ]
        )
        with patch("nci_si_mcp.traversal.BATCH_SIZE", 1):
            result = walk(client, max_depth=1, edge_types=["child"], requests=2)
        self.assertEqual((result.truncation.bound, result.truncation.omitted), ("depth", 1))
        self.assertEqual(len(client.calls), 2)
        self.assertFalse(result.truncation.exact)

    def test_a_prior_node_bound_wins_over_depth_discovered_at_the_frontier(self):
        client = FakeEVS(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2", children=[child("C4")]),
            ]
        )
        result = walk(client, max_depth=1, edge_types=["child"], max_nodes=2)
        self.assertEqual(result.truncation.bound, "nodes")
        self.assertEqual(result.truncation.omitted, 1)
        self.assertEqual(client.includes, ["minimal,children"])

    def test_only_kinds_without_a_prior_cut_are_checked(self):
        client = FakeEVS(
            [
                concept("C1", children=[child("C2"), child("C3")], roles=[related("r", "C4")]),
                concept("C2", children=[child("C5")]),
                concept("C4", roles=[related("r", "C6")]),
            ]
        )
        result = walk(client, max_depth=1, edge_types=["child", "role"], budget_per_kind=1)
        self.assertEqual(client.includes, ["minimal,children,roles", "minimal,roles"])
        cuts = result.truncation.to_dict()["perKind"]
        self.assertEqual(cuts["child"]["bound"], "kind_budget")
        self.assertEqual((cuts["role"]["bound"], cuts["role"]["omitted"]), ("depth", 1))

    def test_an_exact_node_fit_without_a_drop_still_checks_for_continuation(self):
        for children in ([], [child("C3")]):
            with self.subTest(children=children):
                client = FakeEVS(
                    [concept("C1", children=[child("C2")]), concept("C2", children=children)]
                )
                result = walk(client, max_depth=1, edge_types=["child"], max_nodes=2)
                self.assertEqual(client.includes, ["minimal,children", "minimal,children"])
                self.assertEqual(result.truncation.occurred, bool(children))

    def test_an_oversized_check_keeps_an_earlier_kind_cut(self):
        client = BudgetHub(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2", roles=[related("r", "C4")]),
            ]
        )
        result = walk(client, max_depth=1, edge_types=["child", "role"], budget_per_kind=1)
        cuts = result.truncation.to_dict()["perKind"]
        self.assertEqual(result.truncation.bound, "kind_budget")
        self.assertEqual(cuts["child"]["bound"], "kind_budget")
        self.assertEqual(cuts["role"]["bound"], "upstream_cap")
        self.assertFalse(cuts["role"]["exact"])

    def test_inverse_continuation_is_unknown_without_fetching_final_lists(self):
        client = FakeEVS([concept("C1", inverseRoles=[related("r", "C2")])])
        result = walk(client, direction="in", max_depth=1, edge_types=["inverse_role"])
        self.assertEqual(client.includes, ["minimal,inverseRoles"])
        self.assertEqual(
            result.truncation.to_dict(),
            {
                "occurred": True,
                "bound": "depth",
                "limit": 1,
                "reached": 1,
                "omitted": 0,
                "exact": False,
            },
        )

    def test_forward_continuation_is_checked_beside_unknown_inverse_continuation(self):
        client = FakeEVS(
            [
                concept("C1", children=[child("C2")], inverseAssociations=[related("a", "C3")]),
                concept("C2", children=[child("C4")]),
                concept("C3"),
            ]
        )
        result = walk(
            client, direction="both", max_depth=1, edge_types=["child", "inverse_association"]
        )
        self.assertEqual(
            client.includes, ["minimal,children,inverseAssociations", "minimal,children"]
        )
        cuts = result.truncation.to_dict()["perKind"]
        self.assertEqual(cuts["child"]["omitted"], 1)
        self.assertEqual(cuts["inverse_association"]["omitted"], 0)
        self.assertEqual(cuts["inverse_association"]["bound"], "depth")
        self.assertFalse(cuts["inverse_association"]["exact"])

    def test_descendant_continuation_uses_only_the_final_child_lists(self):
        client = FakeEVS(
            [
                concept("C1"),
                concept("C2", children=[child("C3")]),
            ],
            descendants={"C1": [{"code": "C2", "name": "Two", "level": 1}]},
        )
        result = walk(client, max_depth=1, edge_types=["descendant"])
        self.assertEqual(client.includes, ["minimal", "minimal,children"])
        self.assertEqual((result.truncation.bound, result.truncation.omitted), ("depth", 1))
        self.assertEqual(codes(result), ["C1", "C2"])

    def test_an_oversized_final_frontier_is_an_upstream_cap_not_a_depth_claim(self):
        client = BudgetHub(
            [
                concept("C1", children=[child("C2")]),
                concept("C2", children=[child("C3")]),
            ]
        )
        result = walk(client, max_depth=1, edge_types=["child"])
        self.assertEqual(result.truncation.bound, "upstream_cap")
        self.assertEqual(result.truncation.omitted, 1)
        self.assertFalse(result.truncation.exact)
        self.assertIn("C2", result.concepts)

    def test_a_node_cut_skips_even_an_oversized_final_hub(self):
        client = BudgetHub(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2", children=[child("C4")]),
            ]
        )
        result = walk(client, max_depth=1, edge_types=["child"], max_nodes=2)
        self.assertEqual(result.truncation.bound, "nodes")
        self.assertEqual(result.truncation.omitted, 1)
        self.assertFalse(result.truncation.exact)
        self.assertEqual(client.includes, ["minimal,children"])
        self.assertNotIn("C2", result.concepts)

    def test_a_missing_or_unrequested_final_concept_is_not_a_leaf(self):
        root = concept("C1", children=[child("C2")])
        for answer in ([], [concept("C9")]):
            with (
                self.subTest(answer=answer),
                patch.object(FakeEVS, "get_concepts_by_codes", side_effect=[[root], answer]),
                self.assertRaises(EVSResponseError) as raised,
            ):
                walk(FakeEVS(), max_depth=1, edge_types=["child"])
            self.assertIn("EVS", str(raised.exception))

    def test_multiple_seeds_at_depth_zero_are_already_seen_even_across_batches(self):
        client = FakeEVS(
            [
                concept("C1", children=[child("C2")]),
                concept("C2", children=[child("C1")]),
            ]
        )
        with patch("nci_si_mcp.traversal.BATCH_SIZE", 1):
            result = walk(client, start_codes=["C1", "C2"], max_depth=0, edge_types=["child"])
        self.assertEqual(result.truncation.to_dict(), {"occurred": False})
        self.assertEqual(codes(result), ["C1", "C2"])
