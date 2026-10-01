import unittest
from unittest.mock import patch

from fakes import FakeEVS, concept, release

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


def descendant(code, level):
    return {"code": code, "name": f"Concept {code}", "level": level}


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
    """C1 with relations of every kind, each to its own neighbour, and three descendants."""

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
        descendants={"C1": [descendant("C31", 3), descendant("C11", 1), descendant("C21", 2)]},
    )


def walk(client, start_codes=("C1",), direction="out", edge_types=None, **options):
    """Select edge types as the service does, then traverse release 26.06e."""

    selected = select_edge_types(direction, True, True, True, edge_types)
    return traverse_ncit(client, list(start_codes), release(), selected, **options)


def pairs(result):
    return [(edge.source_code, edge.target_code) for edge in result.edges]


def codes(result):
    return [node.code for node in result.nodes]


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
            ("in", True, True, True, ["descendant"]),
            ("out", False, True, True, ["descendant"]),
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

    def test_each_level_is_one_request_pinned_to_the_release(self):
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
        for name in ("Disease_Has_Associated_Gene", "disease_has_associated_gene"):
            with self.subTest(name):
                result = walk(star(), max_depth=1, edge_types=["role"], relationship_names=[name])
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
        client = FakeEVS(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2", children=[child("C4")]),
                concept("C3"),
            ]
        )

        result = walk(client, max_depth=2, max_edges=1)

        self.assertEqual(pairs(result), [("C1", "C2")])
        self.assertEqual(codes(result), ["C1", "C2"])
        self.assertTrue(result.truncated)
        self.assertEqual(result.max_edges, 1)
        self.assertEqual(len(client.calls), 1)

    def test_reaching_the_edge_limit_exactly_is_not_truncation(self):
        result = walk(chain(), max_depth=1, max_edges=1)

        self.assertEqual(pairs(result), [("C1", "C2")])
        self.assertFalse(result.truncated)

    def test_node_limit_drops_edges_to_new_nodes_but_keeps_edges_between_kept_nodes(self):
        client = FakeEVS(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2", children=[child("C1"), child("C9")]),
            ]
        )

        result = walk(client, max_depth=2, max_nodes=2)

        self.assertEqual(pairs(result), [("C1", "C2"), ("C2", "C1")])
        self.assertEqual(codes(result), ["C1", "C2"])
        self.assertTrue(result.truncated)

    def test_start_codes_must_fit_the_node_limit(self):
        with self.assertRaises(InputValidationError):
            walk(chain(), start_codes=["C1", "C2", "C3"], max_nodes=2)

    def test_descendant_edges_link_the_start_code_to_every_level_within_depth(self):
        client = star()

        result = walk(client, max_depth=2, edge_types=["descendant"])

        self.assertEqual(pairs(result), [("C1", "C11"), ("C1", "C21")])
        self.assertEqual({edge.edge_type for edge in result.edges}, {"descendant"})
        self.assertEqual(
            client.calls,
            [
                ("get_concepts_by_codes", "ncit_26.06e", ["C1"]),
                ("get_descendants", "ncit_26.06e", ("C1", 2)),
            ],
        )

    def test_node_limit_keeps_the_nearest_descendants(self):
        result = walk(star(), max_depth=3, max_nodes=3, edge_types=["descendant"])

        self.assertEqual(codes(result), ["C1", "C11", "C21"])
        self.assertTrue(result.truncated)

    def test_descendants_are_requested_once_and_sit_at_their_level(self):
        client = star()
        client.concepts["C11"] = concept("C11", children=[child("C21")])
        client.concepts["C21"] = concept("C21", children=[child("C99")])

        result = walk(client, max_depth=2, edge_types=["child", "descendant"])

        descendant_calls = [call for call in client.calls if call[0] == "get_descendants"]
        self.assertEqual(descendant_calls, [("get_descendants", "ncit_26.06e", ("C1", 2))])
        # C21 is two levels down, so it is at the depth limit and not expanded.
        self.assertNotIn(("C21", "C99"), pairs(result))
        self.assertIn(("C11", "C21"), pairs(result))

    def test_shorter_path_wins_over_a_deeper_descendant_placement(self):
        # C9 is a level-2 descendant of C1 but also one role hop away.
        client = FakeEVS(
            [
                concept("C1", children=[child("C5")], roles=[related("Has_Finding", "C9")]),
                concept("C5", children=[child("C9")]),
                concept("C9", roles=[related("Has_Finding", "C77")]),
            ],
            descendants={"C1": [descendant("C5", 1), descendant("C9", 2)]},
        )

        with_descendants = walk(client, max_depth=2, edge_types=["child", "descendant", "role"])
        without = walk(client, max_depth=2, edge_types=["child", "role"])

        self.assertIn(("C9", "C77"), pairs(with_descendants))
        self.assertEqual(set(codes(with_descendants)), set(codes(without)))
        self.assertFalse(with_descendants.truncated)

    def test_another_start_code_can_bring_a_descendant_closer(self):
        client = FakeEVS(
            [
                concept("C1", children=[child("C5")]),
                concept("C2", children=[child("C9")]),
                concept("C5", children=[child("C9")]),
                concept("C9", children=[child("C99")]),
            ],
            descendants={"C1": [descendant("C5", 1), descendant("C9", 2)], "C2": [descendant("C9", 1)]},
        )

        result = walk(client, start_codes=["C1", "C2"], max_depth=2, edge_types=["child", "descendant"])

        self.assertIn(("C9", "C99"), pairs(result))

    def test_unknown_start_code_is_not_found(self):
        with self.assertRaises(EVSNotFoundError) as raised:
            walk(chain(), start_codes=["C1", "C404"])

        self.assertIn("C404", str(raised.exception))

    def test_relation_to_a_concept_evs_does_not_serve_is_an_evs_fault(self):
        client = FakeEVS([concept("C1", children=[child("C2")])])

        with self.assertRaises(EVSResponseError) as raised:
            walk(client, max_depth=2)

        self.assertNotIsInstance(raised.exception, EVSNotFoundError)
        self.assertIn("C2", str(raised.exception))

    def test_concept_from_another_release_is_rejected(self):
        client = FakeEVS([concept("C1", version="26.07a")])

        with self.assertRaises(EVSResponseError):
            walk(client)

    def test_malformed_relation_list_is_an_evs_fault(self):
        client = FakeEVS([concept("C1", children=["C2"])])

        with self.assertRaises(EVSResponseError):
            walk(client, max_depth=1)

    def test_frontier_is_fetched_in_batches(self):
        client = FakeEVS(
            [concept("C1", children=[child(f"C{n}") for n in range(100, 107)])]
            + [concept(f"C{n}") for n in range(100, 107)]
        )

        with patch("nci_si_mcp.traversal.BATCH_SIZE", 3):
            walk(client, max_depth=2)

        self.assertEqual([len(call[2]) for call in client.calls], [1, 3, 3, 1])

    def test_inward_walks_use_smaller_batches(self):
        parents = [f"C{n}" for n in range(100, 112)]
        client = FakeEVS(
            [concept("C1", parents=[child(code) for code in parents])]
            + [concept(code) for code in parents]
        )

        walk(client, direction="in", max_depth=2)

        self.assertEqual([len(call[2]) for call in client.calls], [1, 10, 2])

    def test_oversized_relations_are_skipped_and_reported(self):
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

        with self.assertLogs("nci_si_mcp.traversal", level="WARNING"):
            result = walk(client, max_depth=2)

        self.assertEqual(pairs(result), [("C1", "C2"), ("C1", "C3"), ("C2", "C4")])
        self.assertTrue(result.truncated)
        self.assertEqual(result.unexpanded_codes, ["C3"])
        self.assertEqual({call[1] for call in client.calls}, {"ncit_26.06e"})

    def test_nothing_unexpanded_when_everything_fits(self):
        result = walk(chain(), max_depth=3)

        self.assertEqual(result.unexpanded_codes, [])
        self.assertEqual(result.to_dict()["unexpanded_codes"], [])


if __name__ == "__main__":
    unittest.main()
