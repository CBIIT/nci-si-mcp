from dataclasses import replace
from unittest.mock import patch

from fakes import concept, terminology_row
from nci_si_mcp.bounds import Budget, current_budget
from nci_si_mcp.registry import invoke
from test_server import ServerFixture, pinned


def relation(code, name="relation"):
    return {"relatedCode": code, "relatedName": code, "type": name}


class ContentMutationTest(ServerFixture):
    def content(self, operation, **arguments):
        return invoke(self.context, operation, **pinned(**arguments))

    def index_concepts(self, concepts):
        self.context.index.upsert_concepts(
            concepts, None, self.context.embedding_provider, expected_release_version="26.06e"
        )

    def children(self, count):
        targets = [{"code": f"C{i}", "name": str(i)} for i in range(2, count + 2)]
        self.evs.concepts = {item["code"]: concept(item["code"], active=True) for item in targets}
        self.evs.concepts["C1"] = concept("C1", active=True, children=targets)

    def test_search_keeps_more_than_one_hundred_hits_and_clamps_large_limits(self):
        self.index_concepts([concept(f"C{i}", "Kinase", active=True) for i in range(1, 151)])
        for limit in (500, 5000):
            with self.subTest(limit=limit):
                result = self.content(
                    "search_concepts", query="Kinase", mode="semantic", limit=limit
                )
                self.assertEqual(len(result["results"]), 150)
                self.assertEqual(result["truncation"], {"occurred": False})

    def test_search_modes_scores_and_source_uris_describe_the_selected_engine(self):
        self.index_concepts(
            [concept("C1", "Kinase", active=True), concept("C2", "Kinase inhibitor", active=True)]
        )
        original = self.context.index.search_with_truncation
        for mode, engine in (("semantic", "vector"), ("hybrid", "hybrid")):
            with (
                self.subTest(mode=mode),
                patch.object(
                    self.context.index, "search_with_truncation", wraps=original
                ) as search,
            ):
                result = self.content("search_concepts", query="Kinase", mode=mode)
                self.assertEqual(search.call_args.args[3], engine)
                hits = result["results"]
                self.assertEqual({hit["concept"]["code"] for hit in hits}, {"C1", "C2"})
                scores = [hit["score"] for hit in hits]
                self.assertGreater(scores[0], 0)
                self.assertEqual(scores, sorted(scores, reverse=True))
                for hit in hits:
                    self.assertTrue(
                        hit["concept"]["provenance"]["sourceUri"].endswith(
                            f"/concept/ncit_26.06e/{hit['concept']['code']}"
                        )
                    )

    def test_unsupported_search_options_are_refused_even_with_a_usable_index(self):
        self.index_concepts([concept("C1", "Kinase", active=True)])
        options = ({"mode": "typeahead"}, {"cursor": "cursor"}, {"retired": "only"})
        for option in options:
            with self.subTest(option=option):
                result = self.content(
                    "search_concepts", **({"query": "Kinase", "mode": "semantic"} | option)
                )
                self.assertIn("error", result)
                self.assertEqual(result["error"]["code"], "capability_unavailable")

    def test_hierarchy_default_depth_is_one_and_large_depth_clamps_to_four(self):
        self.evs.concepts = {
            f"C{i}": concept(f"C{i}", active=True, children=[{"code": f"C{i + 1}"}])
            for i in range(1, 7)
        }
        default = self.content("get_concept_hierarchy", code="C1", direction="child")
        self.assertEqual([node["code"] for node in default["nodes"]], ["C2"])
        result = self.content("get_concept_hierarchy", code="C1", direction="child", depth=9)
        self.assertEqual([node["code"] for node in result["nodes"]], ["C2", "C3", "C4", "C5"])
        self.assertEqual(result["truncation"]["limit"], 4)
        self.assertEqual(result["truncation"]["bound"], "depth")

    def test_hierarchy_default_limit_allows_one_hundred_fifty_children(self):
        self.children(150)
        result = self.content("get_concept_hierarchy", code="C1", direction="child")
        self.assertIn("nodes", result)
        self.assertEqual(len(result["nodes"]), 150)
        self.assertEqual(result["truncation"], {"occurred": False})

    def test_neighborhood_reports_its_hard_node_maximum(self):
        self.children(1000)
        result = self.content(
            "get_concept_neighborhood",
            code="C1",
            kinds=["child"],
            depth=1,
            maxNodes=9000,
            budgetPerKind=9000,
        )
        self.assertEqual(len(result["nodes"]), 1000)
        self.assertEqual(result["truncation"]["bound"], "nodes")
        self.assertEqual(result["truncation"]["limit"], 1000)

    def test_neighborhood_reports_its_hard_edge_maximum(self):
        self.evs.concepts = {
            "C1": concept("C1", active=True, roles=[relation("C1", f"R{i}") for i in range(5001)])
        }
        result = self.content(
            "get_concept_neighborhood", code="C1", kinds=["role"], depth=1, maxEdges=9000
        )
        self.assertEqual(len(result["edges"]), 5000)
        self.assertEqual(result["truncation"]["bound"], "edges")
        self.assertEqual(result["truncation"]["limit"], 5000)

    def test_two_kind_hydration_cut_reports_actual_requests_and_uncertainty(self):
        self.children(2)
        self.evs.concepts["C1"]["roles"] = [relation("C4")]
        original = self.evs.get_concepts_by_codes

        def counted(*args, **kwargs):
            current_budget().request()
            return original(*args, **kwargs)

        with (
            patch("nci_si_mcp.content.Budget", return_value=Budget(depth=1, nodes=3, requests=1)),
            patch.object(self.evs, "get_concepts_by_codes", side_effect=counted),
        ):
            result = self.content("get_concept_neighborhood", code="C1", kinds=["child", "role"])
        self.assertEqual([node["code"] for node in result["nodes"]], ["C1"])
        kinds = result["truncation"]["perKind"]
        self.assertEqual(set(kinds), {"child", "role"})
        self.assertEqual(kinds["child"]["bound"], "nodes")
        self.assertEqual(
            kinds["role"],
            {
                "occurred": True,
                "bound": "requests",
                "limit": 1,
                "reached": 1,
                "omitted": 1,
                "exact": False,
            },
        )

    def test_inverse_associations_use_assertion_endpoints_and_public_kind_names(self):
        self.evs.concepts = {
            "C1": concept("C1", active=True, inverseAssociations=[relation("C2"), relation("C3")]),
            "C2": concept("C2", active=True),
        }
        result = self.content(
            "get_concept_neighborhood",
            code="C1",
            kinds=["child", "inverseAssociation"],
            depth=1,
            budgetPerKind=1,
        )
        (edge,) = result["edges"]
        self.assertEqual((edge["sourceCode"], edge["targetCode"]), ("C2", "C1"))
        self.assertEqual((edge["sourceTerminology"], edge["targetTerminology"]), ("ncit", "ncit"))
        kinds = result["truncation"]["perKind"]
        self.assertEqual(set(kinds), {"child", "inverseAssociation"})
        self.assertEqual(kinds["inverseAssociation"]["bound"], "kind_budget")

    def test_listing_ignores_false_channel_tags_and_uses_the_configured_channel(self):
        self.evs.rows = [
            terminology_row("weekly", monthly="false", weekly="true"),
            terminology_row("monthly", monthly="true", weekly="false"),
        ]
        for channel in ("monthly", "weekly"):
            with self.subTest(channel=channel):
                self.context.settings = replace(self.settings, release_channel=channel)
                result = invoke(self.context, "list_terminologies")
                self.assertIn("terminologies", result)
                self.assertEqual([item["release"] for item in result["terminologies"]], [channel])
