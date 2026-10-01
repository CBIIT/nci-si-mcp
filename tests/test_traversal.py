import unittest
from unittest.mock import patch

from fakes import FakeEVS, concept

from nci_si_mcp.errors import InputValidationError
from nci_si_mcp.evs import EVSNotFoundError, EVSResponseError, EVSResponseTooLargeError
from nci_si_mcp.traversal import (
    HARD_MAX_DEPTH,
    HARD_MAX_EDGES,
    HARD_MAX_NODES,
    clamp_edge_limit,
    clamp_limits,
    select_edge_types,
    traverse_ncit,
)


def child(code):
    return {"code": code, "name": f"Concept {code}", "leaf": False}


def related(name, code):
    return {"code": "R1", "type": name, "relatedCode": code, "relatedName": f"Concept {code}"}


def chain():
    """C1 -> C2 -> C3 -> {C1, C4} over child edges: a cycle with one exit."""

    return FakeEVS(
        [
            concept("C1", children=[child("C2")]),
            concept("C2", children=[child("C3")]),
            concept("C3", children=[child("C1"), child("C4")]),
            concept("C4"),
        ]
    )


def star():
    """C1 with one relation of every kind, each to its own neighbour."""

    return FakeEVS(
        [
            concept(
                "C1",
                "Root",
                parents=[child("C10")],
                children=[child("C11")],
                roles=[
                    related("Disease_Has_Associated_Gene", "C12"),
                    related("Disease_Has_Finding", "C13"),
                ],
                inverseRoles=[related("Gene_Associated_With_Disease", "C14")],
                associations=[related("Concept_In_Subset", "C15")],
                inverseAssociations=[related("Has_GDC_Value", "C16")],
            )
        ],
        descendants={
            "C1": [
                {"code": "C11", "name": "Concept C11", "level": 1},
                {"code": "C21", "name": "Concept C21", "level": 2},
                {"code": "C31", "name": "Concept C31", "level": 3},
            ]
        },
    )


def walk(client, start_codes=("C1",), **options):
    return traverse_ncit(client, list(start_codes), "26.06e", "ncit_26.06e", **options)


def pairs(result):
    return [(edge.source_code, edge.target_code) for edge in result.edges]


class EdgeTypeSelectionTest(unittest.TestCase):
    def test_direction_selects_edge_types_and_descendant_is_opt_in(self):
        self.assertEqual(
            select_edge_types("out", True, True, True, None), ["child", "role", "association"]
        )
        self.assertEqual(
            select_edge_types("in", True, True, True, None),
            ["parent", "inverse_role", "inverse_association"],
        )
        self.assertEqual(len(select_edge_types("both", True, True, True, None)), 6)
        self.assertEqual(select_edge_types("out", True, True, True, ["descendant"]), ["descendant"])

    def test_include_flags_remove_edge_types(self):
        self.assertEqual(select_edge_types("both", False, True, False, None), ["role", "inverse_role"])

    def test_contradictory_selectors_are_rejected(self):
        for arguments in (
            ("out", True, True, True, ["parent"]),
            ("out", True, False, True, ["role"]),
            ("both", False, False, False, None),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(InputValidationError):
                select_edge_types(*arguments)


class TraversalTest(unittest.TestCase):
    def test_clamp_limits_enforces_hard_caps(self):
        self.assertEqual(clamp_limits(99, 99999), (HARD_MAX_DEPTH, HARD_MAX_NODES))
        self.assertEqual(clamp_limits(-1, 0), (0, 1))
        self.assertEqual(clamp_edge_limit(99999), HARD_MAX_EDGES)

    def test_one_batched_request_per_level_pinned_to_the_release(self):
        client = chain()

        result = walk(client, max_depth=2)

        self.assertEqual(
            client.calls,
            [
                ("get_concepts_by_codes", "ncit_26.06e", ["C1"]),
                ("get_concepts_by_codes", "ncit_26.06e", ["C2"]),
            ],
        )
        self.assertEqual(client.includes, ["minimal,children,roles,associations"] * 2)
        self.assertEqual(result.release_version, "26.06e")
        self.assertEqual({node.release_version for node in result.nodes}, {"26.06e"})

    def test_direction_decides_which_relations_are_requested_and_followed(self):
        expected = {
            "out": ("minimal,children,roles,associations", {"C11", "C12", "C13", "C15"}),
            "in": ("minimal,parents,inverseRoles,inverseAssociations", {"C10", "C14", "C16"}),
            "both": (
                "minimal,children,parents,roles,inverseRoles,associations,inverseAssociations",
                {"C10", "C11", "C12", "C13", "C14", "C15", "C16"},
            ),
        }
        for direction, (include, targets) in expected.items():
            with self.subTest(direction=direction):
                client = star()
                result = walk(client, direction=direction, max_depth=1)
                self.assertEqual(client.includes, [include])
                self.assertEqual({edge.target_code for edge in result.edges}, targets)

    def test_edges_carry_type_relationship_name_and_both_concept_names(self):
        result = walk(star(), max_depth=1, edge_types=["role", "child"])

        by_target = {edge.target_code: edge for edge in result.edges}
        self.assertEqual(by_target["C11"].edge_type, "child")
        self.assertEqual(by_target["C11"].relationship_name, "is_a_child")
        self.assertEqual(by_target["C12"].edge_type, "role")
        self.assertEqual(by_target["C12"].relationship_name, "Disease_Has_Associated_Gene")
        self.assertEqual(by_target["C12"].source_name, "Root")
        self.assertEqual(by_target["C12"].target_name, "Concept C12")
        names = {node.code: node.preferred_name for node in result.nodes}
        self.assertEqual(names["C1"], "Root")
        self.assertEqual(names["C12"], "Concept C12")

    def test_relationship_name_filter_ignores_case(self):
        result = walk(
            star(),
            max_depth=1,
            edge_types=["role"],
            relationship_names=["disease_has_associated_gene"],
        )

        self.assertEqual(pairs(result), [("C1", "C12")])

    def test_depth_bounds_a_walk_through_a_cycle(self):
        expected_edges = {
            0: [],
            1: [("C1", "C2")],
            2: [("C1", "C2"), ("C2", "C3")],
            3: [("C1", "C2"), ("C2", "C3"), ("C3", "C1"), ("C3", "C4")],
            99: [("C1", "C2"), ("C2", "C3"), ("C3", "C1"), ("C3", "C4")],
        }
        for depth, edges in expected_edges.items():
            with self.subTest(depth=depth):
                result = walk(chain(), max_depth=depth)
                self.assertEqual(pairs(result), edges)
                self.assertFalse(result.truncated)
        self.assertEqual(walk(chain(), max_depth=99).max_depth, HARD_MAX_DEPTH)

    def test_depth_zero_still_names_and_checks_the_start_codes(self):
        client = chain()

        result = walk(client, max_depth=0)

        self.assertEqual([(node.code, node.preferred_name) for node in result.nodes], [("C1", "Concept C1")])
        self.assertEqual(client.includes, ["minimal"])

    def test_duplicate_start_codes_and_relation_rows_are_emitted_once(self):
        client = FakeEVS(
            [concept("C1", roles=[related("Disease_Has_Finding", "C2")] * 2)]
        )

        result = walk(client, start_codes=["C1", "C1"], max_depth=1)

        self.assertEqual(result.start_codes, ["C1"])
        self.assertEqual(pairs(result), [("C1", "C2")])

    def test_edge_limit_stops_the_walk_and_reports_truncation(self):
        result = walk(star(), max_depth=1, max_edges=1)

        self.assertEqual(len(result.edges), 1)
        self.assertTrue(result.truncated)
        self.assertEqual(result.max_edges, 1)
        codes = {node.code for node in result.nodes}
        self.assertTrue(all(edge.target_code in codes for edge in result.edges))

    def test_node_limit_drops_edges_to_new_nodes_but_keeps_edges_between_kept_nodes(self):
        client = FakeEVS(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2", children=[child("C1"), child("C9")]),
            ]
        )

        result = walk(client, max_depth=2, max_nodes=2)

        self.assertEqual(pairs(result), [("C1", "C2"), ("C2", "C1")])
        self.assertEqual({node.code for node in result.nodes}, {"C1", "C2"})
        self.assertTrue(result.truncated)

    def test_start_codes_must_fit_the_node_limit(self):
        with self.assertRaises(InputValidationError):
            walk(chain(), start_codes=["C1", "C2", "C3"], max_nodes=2)

    def test_descendant_edges_link_the_start_code_to_every_level_within_depth(self):
        client = star()

        result = walk(client, max_depth=2, edge_types=["descendant"])

        self.assertEqual(pairs(result), [("C1", "C11"), ("C1", "C21")])
        self.assertEqual({edge.edge_type for edge in result.edges}, {"descendant"})
        self.assertIn(("get_descendants", "ncit_26.06e", ("C1", 2)), client.calls)

    def test_descendants_are_requested_for_start_codes_only(self):
        client = star()
        client.concepts["C11"] = concept("C11")

        walk(client, max_depth=3, edge_types=["descendant"])

        descendant_calls = [call for call in client.calls if call[0] == "get_descendants"]
        self.assertEqual(descendant_calls, [("get_descendants", "ncit_26.06e", ("C1", 3))])

    def test_unknown_start_code_is_not_found(self):
        with self.assertRaises(EVSNotFoundError) as raised:
            walk(chain(), start_codes=["C1", "C404"])

        self.assertIn("C404", str(raised.exception))

    def test_concept_from_another_release_is_rejected(self):
        client = FakeEVS([concept("C1", version="26.07a")])

        with self.assertRaises(EVSResponseError):
            walk(client)

    def test_frontier_is_fetched_in_batches(self):
        client = FakeEVS(
            [concept("C1", children=[child(f"C{n}") for n in range(100, 107)])]
            + [concept(f"C{n}") for n in range(100, 107)]
        )

        with patch("nci_si_mcp.traversal.BATCH_SIZE", 3):
            walk(client, max_depth=2)

        batches = [call[2] for call in client.calls]
        self.assertEqual([len(batch) for batch in batches], [1, 3, 3, 1])

    def test_oversized_relations_are_skipped_and_reported_as_truncation(self):
        class HubEVS(FakeEVS):
            def get_concepts_by_codes(self, codes, terminology="ncit", include=""):
                codes = list(codes)
                if "C3" in codes and include != "minimal":
                    self._record("get_concepts_by_codes", terminology, codes)
                    raise EVSResponseTooLargeError("too large")
                return super().get_concepts_by_codes(codes, terminology, include)

        client = HubEVS(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2", children=[child("C4")]),
                concept("C3", children=[child("C5")]),
            ]
        )

        with self.assertLogs("nci_si_mcp.traversal", level="WARNING") as logs:
            result = walk(client, max_depth=2)

        self.assertEqual(pairs(result), [("C1", "C2"), ("C1", "C3"), ("C2", "C4")])
        self.assertTrue(result.truncated)
        self.assertIn("C3", logs.output[0])


if __name__ == "__main__":
    unittest.main()
