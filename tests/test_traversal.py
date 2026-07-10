import unittest

from nci_si_mcp.traversal import HARD_MAX_DEPTH, HARD_MAX_NODES, clamp_limits, traverse_ncit


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


if __name__ == "__main__":
    unittest.main()
