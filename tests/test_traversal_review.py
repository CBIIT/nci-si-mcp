"""Core traversal records retain the caller's terminology."""

import unittest

from fakes import FakeEVS, concept, release
from nci_si_mcp.bounds import Budget, budgeted
from nci_si_mcp.errors import correlated
from nci_si_mcp.traversal import traverse_ncit


class TraversalReviewTest(unittest.TestCase):
    def test_other_terminology_is_kept_on_start_and_reached_nodes(self):
        client = FakeEVS(
            [
                concept("root", terminology="other", children=[{"code": "child"}]),
                concept("child", terminology="other"),
            ]
        )
        budget = Budget(depth=1)
        with correlated("other-terminology"), budgeted(budget):
            result = traverse_ncit(
                client,
                ["root"],
                release(terminology="other"),
                ["child"],
                budget,
                exclusions=frozenset(),
            )
        nodes = result.to_dict()["nodes"]
        self.assertEqual(
            [(node["code"], node["terminology"]) for node in nodes],
            [("root", "other"), ("child", "other")],
        )
        self.assertEqual(result.truncation.to_dict(), {"occurred": False})
