import unittest

from nci_si_mcp.traversal import (
    HARD_MAX_DEPTH,
    HARD_MAX_EDGES,
    HARD_MAX_NODES,
    clamp_edge_limit,
    clamp_limits,
    traverse_ncit,
)


class FakeEVS:
    def get_related(self, code, relation):
        if relation == "roles":
            return [
                {
                    "code": "R1",
                    "type": "Disease_Has_Associated_Gene",
                    "relatedCode": "Cgene",
                    "relatedName": "Example Gene",
                },
                {
                    "code": "R2",
                    "type": "Disease_Has_Finding",
                    "relatedCode": "Cfinding",
                    "relatedName": "Example Finding",
                },
            ]
        if relation == "children":
            return [{"code": "Cchild", "name": "Child Concept"}]
        return []


class TraversalTest(unittest.TestCase):
    def test_clamp_limits_enforces_hard_caps(self):
        self.assertEqual(clamp_limits(99, 99999), (HARD_MAX_DEPTH, HARD_MAX_NODES))
        self.assertEqual(clamp_limits(-1, 0), (0, 1))
        self.assertEqual(clamp_edge_limit(99999), HARD_MAX_EDGES)

    def test_traverse_filters_named_relationships(self):
        result = traverse_ncit(
            client=FakeEVS(),
            start_codes=["C3262"],
            release_version="26.06e",
            max_depth=1,
            include_hierarchy=False,
            include_roles=True,
            include_associations=False,
            relationship_names=["Disease_Has_Associated_Gene"],
            edge_types=["role"],
        )

        self.assertEqual(len(result.edges), 1)
        self.assertEqual(result.edges[0].relationship_name, "Disease_Has_Associated_Gene")
        self.assertEqual(result.edges[0].target_code, "Cgene")

    def test_traversal_caps_edges_and_preserves_referential_integrity(self):
        result = traverse_ncit(
            client=FakeEVS(),
            start_codes=["C3262"],
            release_version="26.06e",
            max_depth=1,
            max_nodes=10,
            max_edges=1,
            include_hierarchy=False,
            include_roles=True,
            include_associations=False,
        )

        node_codes = {node.code for node in result.nodes}
        self.assertEqual(len(result.edges), 1)
        self.assertTrue(result.truncated)
        self.assertTrue(all(edge.target_code in node_codes for edge in result.edges))

    def test_node_cap_does_not_emit_dangling_edges(self):
        result = traverse_ncit(
            client=FakeEVS(),
            start_codes=["C3262"],
            release_version="26.06e",
            max_depth=1,
            max_nodes=1,
            max_edges=10,
            include_hierarchy=False,
            include_roles=True,
            include_associations=False,
        )

        self.assertEqual(result.edges, [])
        self.assertTrue(result.truncated)


if __name__ == "__main__":
    unittest.main()
