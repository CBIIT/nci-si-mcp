import unittest
from unittest.mock import patch

from fakes import FakeEVS, concept
from nci_si_mcp.errors import correlated
from nci_si_mcp.evs import EVSResponseError
from test_bounds import BudgetEVS, BudgetHub
from test_traversal import child, descendant, related, walk


class TraversalMutationTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(correlated("mutation-review"))

    def test_unrequested_concept_in_the_initial_batch_is_an_error(self):
        with (
            patch.object(
                FakeEVS, "get_concepts_by_codes", return_value=[concept("C1"), concept("C9")]
            ),
            self.assertRaisesRegex(EVSResponseError, "not requested"),
        ):
            walk(FakeEVS(), max_depth=1, edge_types=["child"])

    def test_final_fetch_caps_preserve_an_earlier_kind_budget(self):
        concepts = [
            concept("C1", roles=[related("r", "C3"), related("r", "C4")]),
            concept("C2"),
            concept("C3"),
        ]
        descendants = {"C1": [descendant("C2", 1)]}
        for client, requests, bound in (
            (BudgetEVS(concepts, descendants=descendants), 2, "requests"),
            (BudgetHub(concepts, descendants=descendants), 200, "upstream_cap"),
        ):
            with self.subTest(bound=bound):
                result = walk(
                    client,
                    max_depth=1,
                    edge_types=["descendant", "role"],
                    budget_per_kind=1,
                    requests=requests,
                )
                records = result.truncation.to_dict()["perKind"]
                self.assertEqual(records["role"]["bound"], "kind_budget")
                self.assertEqual(records["descendant"].get("bound"), bound)
                self.assertEqual(result.truncation.bound, "kind_budget")

    def test_descendant_continuation_is_unknown_only_at_the_final_frontier(self):
        for depth, expected in ((1, True), (2, False)):
            with self.subTest(depth=depth):
                client = FakeEVS(
                    [concept("C1", children=[child("C2")], roles=[related("r", "C3")])]
                )
                result = walk(
                    client,
                    max_depth=depth,
                    max_edges=1,
                    edge_types=["child", "role", "descendant"],
                )
                record = result.truncation.to_dict()["perKind"]["descendant"]
                self.assertEqual(record["occurred"], expected)

    def test_empty_inverse_frontier_is_complete_without_an_extra_read(self):
        client = FakeEVS([concept("C1")])
        result = walk(client, direction="in", max_depth=2, edge_types=["inverse_role"])
        self.assertEqual(result.truncation.to_dict(), {"occurred": False})
        self.assertEqual([node.code for node in result.nodes], ["C1"])
        self.assertEqual(client.includes, ["minimal,inverseRoles"])
