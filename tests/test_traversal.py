import unittest
from unittest.mock import patch

from fakes import FakeEVS, concept, release
from nci_si_mcp.errors import InputValidationError, correlated
from nci_si_mcp.evs import EVSNotFoundError, EVSResponseError, EVSResponseTooLargeError
from nci_si_mcp.traversal import (
    HARD_MAX_DEPTH,
    HARD_MAX_EDGES,
    HARD_MAX_NODES,
    RELATIONS,
    clamp_edge_limit,
    clamp_limits,
    select_edge_types,
    traverse_ncit,
)
from nci_si_mcp.validation import TRAVERSAL_EDGE_TYPES


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


class HubEVS(FakeEVS):
    """A FakeEVS where the relations of the codes in `hubs` exceed the response limit."""

    hubs = frozenset()

    def get_concepts_by_codes(self, codes, terminology="ncit", include=""):
        codes = list(codes)
        if self.hubs.intersection(codes) and include != "minimal":
            self._record("get_concepts_by_codes", terminology, codes)
            raise EVSResponseTooLargeError("too large")
        return super().get_concepts_by_codes(codes, terminology, include)


def walk(client, start_codes=("C1",), direction="out", edge_types=None, **options):
    """Select edge types as the service does, then traverse release 26.06e."""

    selected = select_edge_types(direction, True, True, True, edge_types)
    return traverse_ncit(client, list(start_codes), release(), selected, **options)


def pairs(result):
    return [(edge.source_code, edge.target_code) for edge in result.edges]


def codes(result):
    return [node.code for node in result.nodes]


class EdgeTypeSelectionTest(unittest.TestCase):
    def test_every_public_edge_type_has_a_relation(self):
        self.assertEqual(set(RELATIONS), set(TRAVERSAL_EDGE_TYPES))
        self.assertEqual(
            set(select_edge_types("both", True, True, True, sorted(TRAVERSAL_EDGE_TYPES))),
            set(TRAVERSAL_EDGE_TYPES),
        )

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
        self.assertEqual(
            select_edge_types("both", False, True, False, None), ["role", "inverse_role"]
        )

    def test_contradictory_selectors_are_rejected(self):
        for arguments in (
            ("out", True, True, True, ["parent"]),
            ("in", True, True, True, ["descendant"]),
            ("out", False, True, True, ["descendant"]),
            ("out", True, False, True, ["role"]),
            ("both", False, False, False, None),
        ):
            with (
                self.subTest(arguments=arguments),
                self.assertRaises(InputValidationError) as raised,
            ):
                select_edge_types(*arguments)
            parameter = "include_hierarchy" if arguments[4] is None else "edge_types"
            self.assertEqual(raised.exception.details["parameter"], parameter)


class TraversalTest(unittest.TestCase):
    def setUp(self):
        # The provenance of an item carries the identifier of the call that read it.
        self.enterContext(correlated("call-1"))

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
        self.assertEqual(
            {item.provenance.release["identifier"] for item in [*result.nodes, *result.edges]},
            {"26.06e"},
        )

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
                self.assertFalse(result.truncation.occurred)
        self.assertEqual(walk(chain(), max_depth=99).max_depth, HARD_MAX_DEPTH)

    def test_depth_zero_still_names_and_checks_the_start_codes(self):
        client = chain()

        result = walk(client, max_depth=0)

        self.assertEqual(
            [(node.code, node.preferred_name) for node in result.nodes], [("C1", "Concept C1")]
        )
        self.assertEqual(client.includes, ["minimal"])

    def test_duplicate_relation_rows_are_emitted_once(self):
        client = FakeEVS([concept("C1", roles=[related("Disease_Has_Finding", "C2")] * 2)])

        result = walk(client, max_depth=1)

        self.assertEqual(pairs(result), [("C1", "C2")])

    def test_parallel_edges_between_one_pair_are_all_kept(self):
        client = FakeEVS(
            [
                concept(
                    "C1",
                    children=[child("C2")],
                    roles=[related("Role_A", "C2"), related("Role_B", "C2")],
                )
            ],
            descendants={"C1": [descendant("C2", 1)]},
        )

        result = walk(client, max_depth=1, edge_types=["descendant", "role", "child"])

        # In the order of the edge types as `select_edge_types` lists them, descendants last.
        self.assertEqual(
            [(edge.edge_type, edge.relationship_name) for edge in result.edges],
            [
                ("child", "is_a_child"),
                ("role", "Role_A"),
                ("role", "Role_B"),
                ("descendant", "is_a_descendant"),
            ],
        )
        self.assertFalse(result.truncation.occurred)

    def test_result_reports_the_effective_limits(self):
        result = walk(chain(), max_depth=99, max_nodes=99999, max_edges=99999)

        self.assertEqual(
            (result.max_depth, result.max_nodes, result.max_edges),
            (HARD_MAX_DEPTH, HARD_MAX_NODES, HARD_MAX_EDGES),
        )

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
        self.assertTrue(result.truncation.occurred)
        self.assertEqual(result.max_edges, 1)
        self.assertEqual(len(client.calls), 1)

    def test_reaching_the_edge_limit_exactly_is_not_truncation(self):
        result = walk(chain(), max_depth=1, max_edges=1)

        self.assertEqual(pairs(result), [("C1", "C2")])
        self.assertFalse(result.truncation.occurred)

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
        self.assertTrue(result.truncation.occurred)

    def test_node_limit_keeps_a_later_edge_between_kept_nodes(self):
        client = FakeEVS([concept("C1", children=[child("C3"), child("C2")]), concept("C2")])

        result = walk(client, start_codes=["C1", "C2"], max_depth=1, max_nodes=2)

        self.assertEqual(pairs(result), [("C1", "C2")])
        self.assertTrue(result.truncation.occurred)

    def test_descendants_are_requested_to_the_clamped_depth(self):
        client = FakeEVS(
            [concept("C1")],
            descendants={"C1": [descendant(f"C{level}0", level) for level in range(1, 7)]},
        )

        result = walk(client, max_depth=99, edge_types=["descendant"])

        self.assertIn(("get_descendants", "ncit_26.06e", ("C1", 4)), client.calls)
        self.assertEqual([edge.target_code for edge in result.edges], ["C10", "C20", "C30", "C40"])

    def test_depth_zero_requests_no_descendants(self):
        client = star()

        result = walk(client, max_depth=0, edge_types=["descendant"])

        self.assertEqual([call[0] for call in client.calls], ["get_concepts_by_codes"])
        self.assertEqual(result.edges, [])

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
        self.assertTrue(result.truncation.occurred)

    def test_node_limit_is_spent_nearest_first_across_start_codes(self):
        client = FakeEVS(
            [concept("C1"), concept("C2")],
            descendants={
                "C1": [descendant("C13", 3), descendant("C12", 2), descendant("C11", 1)],
                "C2": [descendant("C21", 1)],
            },
        )

        result = walk(
            client, start_codes=["C1", "C2"], max_depth=3, max_nodes=5, edge_types=["descendant"]
        )

        self.assertEqual(codes(result), ["C1", "C2", "C11", "C21", "C12"])
        self.assertTrue(result.truncation.occurred)

    def test_node_limit_is_spent_nearest_first_across_edge_types(self):
        client = FakeEVS(
            [concept("C1", roles=[related("Has_Finding", "C50")])],
            descendants={"C1": [descendant("C12", 2), descendant("C11", 1), descendant("C13", 3)]},
        )
        client.concepts["C11"] = concept("C11")
        client.concepts["C50"] = concept("C50")

        result = walk(client, max_depth=3, max_nodes=3, edge_types=["descendant", "role"])

        self.assertEqual(set(codes(result)), {"C1", "C11", "C50"})
        self.assertTrue(result.truncation.occurred)

    def test_descendant_without_a_usable_level_is_an_evs_fault(self):
        for level in (None, 0, -1, 3, "2", True):
            client = FakeEVS([concept("C1")])
            client.get_descendants = lambda code, max_level, terminology, level=level: [
                {"code": "C9", "name": "Concept C9", "level": level}
            ]
            with self.subTest(level=level), self.assertRaises(EVSResponseError):
                walk(client, max_depth=2, edge_types=["descendant"])

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
        self.assertFalse(with_descendants.truncation.occurred)

    def test_another_start_code_can_bring_a_descendant_closer(self):
        client = FakeEVS(
            [
                concept("C1", children=[child("C5")]),
                concept("C2", children=[child("C9")]),
                concept("C5", children=[child("C9")]),
                concept("C9", children=[child("C99")]),
            ],
            descendants={
                "C1": [descendant("C5", 1), descendant("C9", 2)],
                "C2": [descendant("C9", 1)],
            },
        )

        result = walk(
            client, start_codes=["C1", "C2"], max_depth=2, edge_types=["child", "descendant"]
        )

        self.assertIn(("C9", "C99"), pairs(result))

    def test_a_longer_path_found_later_does_not_push_a_node_deeper(self):
        client = FakeEVS(
            [
                concept("C1", children=[child("C5")]),
                concept("C2", children=[child("C9")]),
                concept("C5", children=[child("C9")]),
                concept("C9", children=[child("C99")]),
            ],
            descendants={
                "C1": [descendant("C5", 1), descendant("C9", 2)],
                "C2": [descendant("C9", 1)],
            },
        )

        result = walk(
            client, start_codes=["C2", "C1"], max_depth=2, edge_types=["child", "descendant"]
        )

        self.assertIn(("C9", "C99"), pairs(result))

    def test_a_deeper_descendant_is_expanded_at_its_own_level(self):
        client = FakeEVS(
            [concept("C1"), concept("C11"), concept("C21", roles=[related("Has_Finding", "C50")])],
            descendants={"C1": [descendant("C11", 1), descendant("C21", 2)]},
        )

        result = walk(client, max_depth=3, edge_types=["descendant", "role"])

        self.assertIn(("C21", "C50"), pairs(result))
        self.assertEqual(
            [call[2] for call in client.calls if call[0] == "get_concepts_by_codes"],
            [["C1"], ["C11"], ["C21"]],
        )

    def test_a_node_reached_by_two_paths_is_fetched_once(self):
        client = FakeEVS(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2", children=[child("C4")]),
                concept("C3", children=[child("C4")]),
                concept("C4", children=[child("C5")]),
            ]
        )

        result = walk(client, max_depth=3)

        self.assertEqual([call[2] for call in client.calls], [["C1"], ["C2", "C3"], ["C4"]])
        self.assertEqual(
            pairs(result), [("C1", "C2"), ("C1", "C3"), ("C2", "C4"), ("C3", "C4"), ("C4", "C5")]
        )

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

    def test_every_concept_of_a_level_is_checked_against_the_release(self):
        client = FakeEVS(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2"),
                concept("C3", version="26.07a"),
            ]
        )

        with self.assertRaises(EVSResponseError) as raised:
            walk(client, max_depth=2)

        self.assertIn("26.07a", str(raised.exception))

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

    def test_only_inverse_relations_use_the_smaller_batches(self):
        neighbours = [f"C{number}" for number in range(100, 112)]

        def inverse(code):
            return related("Has_GDC_Value", code)

        expected = {
            "parent": ("parents", child, [1, 12]),
            "inverse_role": ("inverseRoles", inverse, [1, 10, 2]),
            "inverse_association": ("inverseAssociations", inverse, [1, 10, 2]),
        }
        for edge_type, (field, relation, sizes) in expected.items():
            with self.subTest(edge_type):
                client = FakeEVS(
                    [concept("C1", **{field: [relation(code) for code in neighbours]})]
                    + [concept(code) for code in neighbours]
                )
                walk(client, direction="in", max_depth=2, edge_types=[edge_type])
                self.assertEqual([len(call[2]) for call in client.calls], sizes)

    def test_oversized_relations_are_skipped_and_reported(self):
        client = HubEVS(
            [
                concept("C1", children=[child("C2"), child("C3")]),
                concept("C2", children=[child("C4")]),
                concept("C3", children=[child("C5")]),
            ]
        )
        client.hubs = frozenset({"C3"})

        with self.assertLogs("nci_si_mcp.traversal", level="WARNING"):
            result = walk(client, max_depth=2)

        self.assertEqual(pairs(result), [("C1", "C2"), ("C1", "C3"), ("C2", "C4")])
        self.assertTrue(result.truncation.occurred)
        self.assertEqual((result.truncation.bound, result.truncation.omitted), ("upstream_cap", 1))
        self.assertEqual({call[1] for call in client.calls}, {"ncit_26.06e"})

    def test_oversized_descendants_are_reported_and_the_walk_continues(self):
        client = star()
        client.errors = {"get_descendants": EVSResponseTooLargeError("too large")}

        with self.assertLogs("nci_si_mcp.traversal", level="WARNING") as logs:
            result = walk(client, max_depth=1, edge_types=["child", "descendant"])

        self.assertEqual(pairs(result), [("C1", "C11")])
        self.assertTrue(result.truncation.occurred)
        self.assertEqual((result.truncation.bound, result.truncation.omitted), ("upstream_cap", 1))
        self.assertIn("code=C1 reason=too large", logs.output[-1])

    def test_every_oversized_concept_of_a_batch_is_reported(self):
        client = HubEVS(
            [concept("C1", children=[child("C2"), child("C3"), child("C4")])]
            + [concept(code, children=[child(f"{code}9")]) for code in ("C2", "C3", "C4")]
        )
        client.hubs = frozenset({"C2", "C4"})

        with self.assertLogs("nci_si_mcp.traversal", level="WARNING"):
            result = walk(client, max_depth=2)

        self.assertEqual((result.truncation.bound, result.truncation.omitted), ("upstream_cap", 2))
        self.assertTrue(result.truncation.occurred)
        self.assertEqual(pairs(result)[-1], ("C3", "C39"))

    def test_an_oversized_start_code_is_named_and_reported(self):
        client = HubEVS([concept("C1", "Hub", children=[child("C2")])])
        client.hubs = frozenset({"C1"})

        with self.assertLogs("nci_si_mcp.traversal", level="WARNING"):
            result = walk(client, max_depth=1)

        self.assertEqual(
            [(node.code, node.preferred_name) for node in result.nodes], [("C1", "Hub")]
        )
        self.assertEqual(result.edges, [])
        self.assertTrue(result.truncation.occurred)
        self.assertEqual((result.truncation.bound, result.truncation.omitted), ("upstream_cap", 1))

    def test_oversized_descendants_of_one_start_code_do_not_hide_the_others(self):
        class Hub(FakeEVS):
            def get_descendants(self, code, max_level, terminology="ncit"):
                if code == "C1":
                    raise EVSResponseTooLargeError("too large")
                return super().get_descendants(code, max_level, terminology)

        client = Hub([concept("C1"), concept("C2")], descendants={"C2": [descendant("C21", 1)]})

        with self.assertLogs("nci_si_mcp.traversal", level="WARNING"):
            result = walk(client, start_codes=["C1", "C2"], max_depth=1, edge_types=["descendant"])

        self.assertEqual(pairs(result), [("C2", "C21")])
        self.assertEqual((result.truncation.bound, result.truncation.omitted), ("upstream_cap", 1))

    def test_other_evs_faults_are_not_reported_as_oversized(self):
        client = star()
        client.errors = {"get_descendants": EVSResponseError("HTTP 400")}
        with self.assertRaises(EVSResponseError):
            walk(client, max_depth=1, edge_types=["descendant"])

        class RejectsRelations(FakeEVS):
            def get_concepts_by_codes(self, codes, terminology="ncit", include=""):
                if include != "minimal":
                    raise EVSResponseError("HTTP 400")
                return super().get_concepts_by_codes(codes, terminology, include)

        with self.assertRaises(EVSResponseError):
            walk(RejectsRelations([concept("C1", children=[child("C2")])]), max_depth=1)

    def test_a_start_code_oversized_in_both_respects_is_listed_once(self):
        client = HubEVS([concept("C1", children=[child("C2")])])
        client.hubs = frozenset({"C1"})
        client.errors = {"get_descendants": EVSResponseTooLargeError("too large")}

        with self.assertLogs("nci_si_mcp.traversal", level="WARNING") as logs:
            result = walk(client, max_depth=1, edge_types=["child", "descendant"])

        self.assertEqual((result.truncation.bound, result.truncation.omitted), ("upstream_cap", 1))
        self.assertEqual(pairs(result), [])
        self.assertIn("reason=too large", logs.output[-1])

    def test_a_relation_without_a_target_code_is_an_invalid_response(self):
        # A role item carries the code of its relationship, which is not a target.
        role = {"code": "R105", "type": "Disease_Has_Abnormal_Cell", "relatedName": "Cell"}
        nameless = {"name": "Nameless", "level": 1}
        for edge_type, item in {"child": nameless, "role": role, "descendant": nameless}.items():
            with self.subTest(edge_type):
                client = FakeEVS(
                    [concept("C1", children=[item], roles=[item])], descendants={"C1": [item]}
                )

                with self.assertRaises(EVSResponseError) as raised:
                    walk(client, max_depth=1, edge_types=[edge_type])

                key = "relatedCode" if edge_type == "role" else "code"
                self.assertIn(f"{edge_type} relation of C1 without a {key}", str(raised.exception))

    def test_nothing_unexpanded_when_everything_fits(self):
        result = walk(chain(), max_depth=3)

        self.assertEqual(result.to_dict()["truncation"], {"occurred": False})

    def test_hierarchy_edges_carry_their_documented_names(self):
        result = walk(
            star(), direction="both", max_depth=1, edge_types=["parent", "child", "descendant"]
        )

        self.assertEqual(
            sorted(
                (edge.edge_type, edge.relationship_name, edge.target_code) for edge in result.edges
            ),
            [
                ("child", "is_a_child", "C11"),
                ("descendant", "is_a_descendant", "C11"),
                ("parent", "is_a_parent", "C10"),
            ],
        )
        filtered = walk(star(), direction="in", max_depth=1, relationship_names=["IS_A_PARENT"])
        self.assertEqual(pairs(filtered), [("C1", "C10")])

    def test_name_filter_applies_to_descendant_edges(self):
        by_role = walk(
            star(),
            max_depth=1,
            edge_types=["descendant", "role"],
            relationship_names=["Disease_Has_Finding"],
        )
        self.assertEqual(pairs(by_role), [("C1", "C13")])

        by_descendant = walk(
            star(),
            max_depth=1,
            edge_types=["descendant", "role"],
            relationship_names=["is_a_descendant"],
        )
        self.assertEqual(pairs(by_descendant), [("C1", "C11")])

    def test_an_oversized_batch_is_halved_and_every_step_is_logged(self):
        children = [f"C{number}" for number in range(10, 18)]
        client = HubEVS(
            [concept("C1", children=[child(code) for code in children])]
            + [concept(code) for code in children]
        )
        client.hubs = frozenset({"C17"})

        with self.assertLogs("nci_si_mcp.traversal", level="INFO") as logs:
            result = walk(client, max_depth=2)

        # One request for C1, then 8 -> 4 + 4 -> 2 + 2 -> 1 + 1, and the minimal re-read of C17.
        self.assertEqual([len(call[2]) for call in client.calls], [1, 8, 4, 4, 2, 2, 1, 1, 1])
        self.assertEqual((result.truncation.bound, result.truncation.omitted), ("upstream_cap", 1))
        too_large = [line for line in logs.output if "traverse_batch_too_large" in line]
        self.assertEqual(len(too_large), 4)
        self.assertIn("concepts=8 reason=too large", too_large[0])
        self.assertIn("traverse_relations_too_large codes=C17", logs.output[-1])

    def test_outward_walks_fetch_fifty_concepts_per_request(self):
        children = [f"C{number}" for number in range(100, 151)]
        client = FakeEVS(
            [concept("C1", children=[child(code) for code in children])]
            + [concept(code) for code in children]
        )

        walk(client, max_depth=2)

        self.assertEqual([len(call[2]) for call in client.calls], [1, 50, 1])

    def test_hard_caps_are_the_documented_ones(self):
        self.assertEqual((HARD_MAX_DEPTH, HARD_MAX_NODES, HARD_MAX_EDGES), (4, 1000, 5000))
        self.assertEqual(clamp_edge_limit(0), 1)

    def test_a_relation_without_a_type_is_named_after_its_edge_type(self):
        untyped = {"relatedCode": "C2", "relatedName": "Concept C2"}
        fields = {
            "role": ("out", "roles"),
            "association": ("out", "associations"),
            "inverse_role": ("in", "inverseRoles"),
            "inverse_association": ("in", "inverseAssociations"),
        }
        for edge_type, (direction, field) in fields.items():
            with self.subTest(edge_type):
                client = FakeEVS([concept("C1", **{field: [untyped]})])
                result = walk(client, direction=direction, max_depth=1, edge_types=[edge_type])
                self.assertEqual([edge.relationship_name for edge in result.edges], [edge_type])

    def test_a_duplicate_row_at_the_edge_limit_is_not_truncation(self):
        client = FakeEVS([concept("C1", roles=[related("Has_Finding", "C2")] * 2)])

        result = walk(client, max_depth=1, max_edges=1)

        self.assertEqual(pairs(result), [("C1", "C2")])
        self.assertFalse(result.truncation.occurred)

    def test_every_unknown_start_code_is_named(self):
        client = FakeEVS([concept("C1")])

        with self.assertRaises(EVSNotFoundError) as raised:
            walk(client, start_codes=["C1", "C404", "C405"])

        self.assertIn("C404, C405", str(raised.exception))

    def test_a_role_target_is_named_by_its_related_name(self):
        row = dict(related("Has_Finding", "C2"), name="Has Finding (role name)")
        client = FakeEVS([concept("C1", roles=[row])])

        result = walk(client, max_depth=1, edge_types=["role"])

        self.assertEqual(result.edges[0].target_name, "Concept C2")

    def test_every_edge_type_names_its_target(self):
        result = walk(star(), direction="both", max_depth=1, edge_types=sorted(RELATIONS))

        self.assertEqual(
            sorted((edge.edge_type, edge.target_code, edge.target_name) for edge in result.edges),
            [
                ("association", "C15", "Concept C15"),
                ("child", "C11", "Concept C11"),
                ("descendant", "C11", "Concept C11"),
                ("inverse_association", "C16", "Concept C16"),
                ("inverse_role", "C14", "Concept C14"),
                ("parent", "C10", "Concept C10"),
                ("role", "C12", "Concept C12"),
                ("role", "C13", "Concept C13"),
            ],
        )
        self.assertEqual(
            {node.code: node.preferred_name for node in result.nodes},
            dict(
                {"C1": "Root"}, **{f"C{number}": f"Concept C{number}" for number in range(10, 17)}
            ),
        )

    def test_a_descendant_reached_by_no_other_edge_is_named(self):
        result = walk(star(), max_depth=3, edge_types=["descendant"])

        self.assertEqual(
            {node.code: node.preferred_name for node in result.nodes},
            {"C1": "Root", "C11": "Concept C11", "C21": "Concept C21", "C31": "Concept C31"},
        )

    def test_a_role_without_a_related_name_is_not_named_after_the_role(self):
        row = {"code": "R1", "type": "Has_Finding", "relatedCode": "C2", "name": "Has Finding"}
        client = FakeEVS([concept("C1", roles=[row])])

        result = walk(client, max_depth=1, edge_types=["role"])

        self.assertEqual(result.edges[0].target_name, "")

    def test_the_oversized_relations_warning_names_every_code_and_the_setting(self):
        client = HubEVS(
            [concept("C1", children=[child("C2"), child("C3"), child("C4")])]
            + [concept(code) for code in ("C2", "C3", "C4")]
        )
        client.hubs = frozenset({"C2", "C4"})

        with self.assertLogs("nci_si_mcp.traversal", level="WARNING") as logs:
            walk(client, max_depth=2)

        self.assertIn(
            "traverse_relations_too_large codes=C2,C4 limit=NCI_SI_EVS_MAX_RESPONSE_BYTES",
            logs.output[-1],
        )


if __name__ == "__main__":
    unittest.main()
