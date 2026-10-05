from unittest.mock import patch

from jsonschema import Draft202012Validator

from fakes import concept, release
from nci_si_mcp.bounds import Budget, current_budget
from nci_si_mcp.models import Truncation
from nci_si_mcp.registry import invoke
from test_bounds import BudgetHub
from test_server import ServerFixture


class ContentTest(ServerFixture):
    def setUp(self):
        super().setUp()
        self.evs.concepts = {
            "C1": concept(
                "C1",
                "One",
                active=True,
                conceptStatus="Header_Concept",
                children=[{"code": "C2", "name": "Two"}],
            ),
            "C2": concept("C2", "Two", active=False, conceptStatus="Retired_Concept"),
        }

    def content(self, operation, **arguments):
        return invoke(self.context, operation, terminology="ncit", release="26.06e", **arguments)

    def test_lookup_pins_requested_release_without_resolving_current(self):
        self.evs.release = release("new")
        result = self.content("get_concept", code="C1")
        self.assertEqual(
            {k: result[k] for k in ("code", "name", "active", "status")},
            {"code": "C1", "name": "One", "active": True, "status": "Header_Concept"},
        )
        self.assertEqual(result["provenance"]["release"]["identifier"], "26.06e")
        self.assertEqual(self.evs.calls, [("get_concept", "ncit_26.06e", "C1")])
        self.assertNotIn("properties", result)

    def test_selected_sections_are_unchanged_and_semantic_type_is_selected_by_code(self):
        sections = {
            "synonyms": [{"name": "First", "source": "NCI"}],
            "definitions": [{"definition": "One"}],
            "properties": [
                {"code": "P106", "value": "Kind", "type": "renamed"},
                {"code": "P1", "value": "Wrong", "type": "Semantic_Type"},
            ],
        }
        self.evs.concepts["C1"].update(sections)
        result = self.content("get_concept", code="C1", include=[*sections, "semanticType"])
        self.assertEqual({key: result[key] for key in sections}, sections)
        self.assertEqual(result["semanticType"], ["Kind"])
        self.assertEqual(self.evs.includes, ["minimal,synonyms,definitions,properties"])

    def test_missing_selected_sections_are_empty_and_missing_status_is_omitted(self):
        del self.evs.concepts["C1"]["conceptStatus"]
        result = self.content("get_concept", code="C1", include=["synonyms", "semanticType"])
        self.assertEqual(result["synonyms"], [])
        self.assertEqual(result["semanticType"], [])
        self.assertNotIn("status", result)

    def test_invalid_content_identifiers_do_not_reach_evs(self):
        for field, value in (
            ("code", "c1"),
            ("code", "C0"),
            ("code", "C1\n"),
            ("release", "../version"),
            ("release", "v\n"),
            ("terminology", "NCIT"),
            ("include", ["descendants"]),
        ):
            arguments = {"terminology": "ncit", "release": "26.06e", "code": "C1", field: value}
            with self.subTest(field=field, value=value):
                result = invoke(self.context, "get_concept", **arguments)
                self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(self.evs.calls, [])

    def test_wrong_release_or_identity_never_becomes_a_success(self):
        self.evs.concepts["C1"]["version"] = "other"
        result = self.content("get_concept", code="C1")
        self.assertEqual(result["error"]["code"], "release_mismatch")
        self.evs.concepts["C1"].update(version="26.06e", code="C9")
        result = self.content("get_concept", code="C1")
        self.assertEqual(result["error"]["code"], "upstream_unavailable")

    def test_malformed_concept_fails_instead_of_inventing_status_or_name(self):
        for field in ("active", "name", "terminology"):
            with self.subTest(field=field):
                original = self.evs.concepts["C1"].pop(field)
                result = self.content("get_concept", code="C1")
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.evs.concepts["C1"][field] = original

    def test_unsupported_terminology_is_an_explicit_capability_error(self):
        result = invoke(self.context, "get_concept", terminology="other", release="v1", code="X")
        self.assertEqual(result["error"]["code"], "capability_unavailable")
        self.assertEqual(self.evs.calls, [])

    def test_required_release_and_wrong_types_are_correlated_protocol_errors(self):
        calls = [
            ("get_concept", {"terminology": "ncit", "code": "C1"}, "release"),
            ("get_concept", {"terminology": "ncit", "release": "26.06e", "code": 1}, "code"),
            (
                "get_concept",
                {
                    "terminology": "ncit",
                    "release": "26.06e",
                    "code": "C1",
                    "include": ["descendants"],
                },
                "include",
            ),
            (
                "get_concept_neighborhood",
                {"terminology": "ncit", "release": "26.06e", "code": "C1", "maxNodes": True},
                "maxNodes",
            ),
        ]
        for name, arguments, field in calls:
            with self.subTest(name=name, field=field):
                result = self.session(
                    lambda client, name=name, arguments=arguments: client.call_tool(
                        name, arguments, meta={"correlationId": "invalid"}
                    )
                )
                self.assertTrue(result.is_error)
                error = result.structured_content["error"]
                self.assertEqual(
                    (error["code"], error["correlationId"]), ("invalid_request", "invalid")
                )
                self.assertEqual(error["details"]["parameter"], field)
                self.assertEqual((result.meta["ttlMs"], result.meta["cacheScope"]), (0, "private"))
        self.assertEqual(self.evs.calls, [])

    def test_empty_search_has_provenance_and_detects_a_replaced_index(self):
        self.index()
        with patch.object(
            self.context.index, "search_with_truncation", return_value=([], Truncation(False))
        ):
            result = self.content("search_concepts", query="No match", mode="semantic")
            self.assertEqual(result["results"], [])
            self.assertEqual(result["provenance"]["release"]["identifier"], "26.06e")
            self.evs.concepts = {"C3": concept("C3", version="new", active=True)}
            self.context.index.upsert_concepts(
                list(self.evs.concepts.values()), None, self.context.embedding_provider
            )
            result = self.content("search_concepts", query="No match", mode="semantic")
            self.assertEqual(result["error"]["code"], "release_mismatch")
            with patch.object(self.context.index, "get_active_manifest", return_value=None):
                result = self.content("search_concepts", query="No match", mode="semantic")
                self.assertEqual(result["error"]["code"], "capability_unavailable")

    def test_empty_hierarchy_is_an_attributed_success(self):
        result = self.content("get_concept_hierarchy", code="C2", direction="parent")
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["truncation"], {"occurred": False})
        self.assertEqual(
            result["provenance"]["upstream"], {"terminology": "ncit", "version": "26.06e"}
        )

    def test_hierarchy_needing_a_second_page_does_not_claim_completion(self):
        self.evs.concepts["C1"]["children"].append({"code": "C3", "name": "Three"})
        result = self.content("get_concept_hierarchy", code="C1", direction="child", limit=1)
        self.assertEqual(result["error"]["code"], "capability_unavailable")
        self.assertEqual(result["error"]["details"], {"capability": "hierarchy paging"})

    def test_hierarchy_maximum_page_excludes_seed_from_its_allowance(self):
        children = [{"code": f"C{i}", "name": str(i)} for i in range(2, 1002)]
        self.evs.concepts = {item["code"]: concept(item["code"], active=True) for item in children}
        self.evs.concepts["C1"] = concept("C1", active=True, children=children)
        result = self.content("get_concept_hierarchy", code="C1", direction="child", limit=1000)
        self.assertEqual(
            {node["code"] for node in result["nodes"]}, set(self.evs.concepts) - {"C1"}
        )
        self.assertEqual(result["truncation"], {"occurred": False})
        children.append({"code": "C1002", "name": "Extra"})
        result = self.content("get_concept_hierarchy", code="C1", direction="child", limit=1000)
        self.assertEqual(result["error"]["details"], {"capability": "hierarchy paging"})

    def test_hierarchy_shared_descendants_do_not_consume_a_hidden_edge_limit(self):
        middle = [{"code": f"C{i}", "name": str(i)} for i in range(2, 36)]
        leaves = [{"code": f"C{i}", "name": str(i)} for i in range(36, 67)]
        self.evs.concepts = {item["code"]: concept(item["code"], active=True) for item in leaves}
        self.evs.concepts.update(
            {
                item["code"]: concept(item["code"], active=True, children=list(leaves))
                for item in middle
            }
        )
        self.evs.concepts["C1"] = concept("C1", active=True, children=middle)
        self.evs.concepts["C35"]["children"].append({"code": "C67", "name": "Last"})
        self.evs.concepts["C67"] = concept("C67", active=True)
        result = self.content("get_concept_hierarchy", code="C1", direction="child", depth=2)
        self.assertEqual(
            {node["code"] for node in result["nodes"]}, set(self.evs.concepts) - {"C1"}
        )
        self.assertEqual(result["truncation"], {"occurred": False})

    def test_hierarchy_refuses_paging_even_when_an_earlier_bound_wins(self):
        self.context.evs = BudgetHub(
            [
                concept("C1", active=True, children=[{"code": "C2"}, {"code": "C3"}]),
                concept("C2", active=True),
                concept("C3", active=True, children=[{"code": "C4"}, {"code": "C5"}]),
                concept("C4", active=True),
            ]
        )
        result = self.content(
            "get_concept_hierarchy", code="C1", direction="child", depth=2, limit=3
        )
        self.assertEqual(result["error"]["code"], "capability_unavailable")
        self.assertEqual(result["error"]["details"], {"capability": "hierarchy paging"})

    def test_neighborhood_rejects_empty_kinds_and_nonboolean_negative_flag(self):
        for arguments in (
            {"kinds": []},
            {"kinds": ["invented"]},
            {"includeNegative": 1},
            {"maxNodes": 0},
            {"depth": 0},
            {"budgetPerKind": 0},
        ):
            result = self.content("get_concept_neighborhood", code="C1", **arguments)
            self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(self.evs.calls, [])

    def test_kind_budget_reports_public_kind_names(self):
        self.evs.concepts["C3"] = concept("C3", active=True)
        self.evs.concepts["C1"]["inverseRoles"] = [
            {"code": "R1", "relatedCode": "C2", "relatedName": "Two"},
            {"code": "R1", "relatedCode": "C3", "relatedName": "Three"},
            {"code": "R1", "relatedCode": "C4", "relatedName": "Four"},
        ]
        result = self.content(
            "get_concept_neighborhood", code="C1", kinds=["child", "inverseRole"], budgetPerKind=1
        )
        self.assertEqual(result["truncation"]["perKind"]["inverseRole"]["occurred"], True)
        self.assertEqual(result["truncation"]["perKind"]["inverseRole"]["omitted"], 1)
        self.assertNotIn("inverse_role", result["truncation"].get("perKind", {}))

    def test_missing_node_payload_is_an_upstream_failure(self):
        del self.evs.concepts["C2"]
        result = self.content("get_concept_neighborhood", code="C1", kinds=["child"], depth=1)
        self.assertEqual(result["error"]["code"], "upstream_unavailable")

    def index(self):
        self.context.index.upsert_concepts(
            list(self.evs.concepts.values()),
            None,
            self.context.embedding_provider,
            expected_release_version="26.06e",
        )

    def test_search_preserves_indexed_status_and_refuses_other_release(self):
        self.index()
        for mode in ("semantic", "hybrid"):
            result = self.content("search_concepts", query="One", mode=mode, limit=9000)
            self.assertEqual({hit["concept"]["code"] for hit in result["results"]}, {"C1", "C2"})
            self.assertEqual(
                {
                    hit["concept"]["code"]: (hit["concept"]["active"], hit["concept"]["status"])
                    for hit in result["results"]
                },
                {"C1": (True, "Header_Concept"), "C2": (False, "Retired_Concept")},
            )
            self.assertEqual(result["results"][0]["concept"]["provenance"]["servedBy"], "index")
        mismatch = invoke(
            self.context,
            "search_concepts",
            terminology="ncit",
            release="old",
            query="One",
            mode="semantic",
        )
        self.assertEqual(mismatch["error"]["code"], "release_mismatch")
        self.assertEqual(
            mismatch["error"]["details"],
            {"requested": "old", "served": ["26.06e"], "source": "index"},
        )
        self.assertEqual(self.evs.calls, [])

    def test_search_refuses_unimplemented_options_and_invalid_inputs(self):
        for arguments in (
            {},
            {"mode": "typeahead"},
            {"mode": "hybrid", "cursor": ""},
            {"mode": "semantic", "retired": "only"},
        ):
            result = self.content("search_concepts", query="One", **arguments)
            self.assertEqual(result["error"]["code"], "capability_unavailable")
        for arguments in ({"limit": 0}, {"retired": "exclude"}, {"mode": "bm25"}):
            result = self.content("search_concepts", query="One", **arguments)
            self.assertEqual(result["error"]["code"], "invalid_request")
        result = self.content("search_concepts", query=" ", mode="semantic")
        self.assertEqual(result["error"]["code"], "invalid_request")

    def test_hierarchy_excludes_seed_and_hydrates_final_nodes_once(self):
        result = self.content("get_concept_hierarchy", code="C1", direction="child")
        self.assertEqual([(n["code"], n["active"]) for n in result["nodes"]], [("C2", False)])
        self.assertEqual(result["nodes"][0]["provenance"]["depth"], 1)
        self.assertEqual(result["nodes"][0]["provenance"]["direction"], "in")
        self.assertEqual(
            result["nodes"][0]["provenance"]["upstream"],
            {"terminology": "ncit", "version": "26.06e"},
        )
        self.assertEqual(self.evs.includes, ["minimal,children", "minimal,children"])

    def test_an_edge_stop_still_fetches_the_status_of_returned_nodes(self):
        self.evs.concepts["C1"]["children"].append({"code": "C3", "name": "Three"})
        result = self.content(
            "get_concept_neighborhood", code="C1", kinds=["child"], depth=1, maxEdges=1
        )
        self.assertEqual(
            [(n["code"], n["active"]) for n in result["nodes"]], [("C1", True), ("C2", False)]
        )
        self.assertEqual(result["truncation"]["bound"], "edges")
        self.assertEqual(self.evs.includes, ["minimal,children", "minimal"])

    def test_status_fetch_after_an_edge_stop_rejects_missing_or_wrong_release_nodes(self):
        root = dict(self.evs.concepts["C1"], children=[{"code": "C2"}, {"code": "C3"}])
        for answer, expected in (
            ([], "upstream_unavailable"),
            ([concept("C9")], "upstream_unavailable"),
            ([concept("C2", version="26.01a")], "release_mismatch"),
        ):
            with (
                self.subTest(expected=expected),
                patch.object(self.evs, "get_concepts_by_codes", side_effect=[[root], answer]),
            ):
                result = self.content(
                    "get_concept_neighborhood", code="C1", kinds=["child"], depth=1, maxEdges=1
                )
                self.assertEqual(result["error"]["code"], expected)

    def test_neighborhood_includes_seed_and_edges_follow_assertions(self):
        result = self.content("get_concept_neighborhood", code="C1", kinds=["child"], depth=1)
        self.assertEqual({n["code"] for n in result["nodes"]}, {"C1", "C2"})
        self.assertEqual(
            (result["edges"][0]["sourceCode"], result["edges"][0]["targetCode"]), ("C2", "C1")
        )
        self.assertEqual(result["edges"][0]["provenance"]["relationship"], {"kind": "child"})

    def test_inverse_assertions_reverse_endpoints_and_negative_walk_requires_opt_in(self):
        self.evs.concepts["C1"]["inverseRoles"] = [
            {"code": "R135", "type": "Exclusion", "relatedCode": "C2", "relatedName": "Two"}
        ]
        refused = self.content("get_concept_neighborhood", code="C1", kinds=["inverseRole"])
        self.assertEqual(refused["error"]["code"], "capability_unavailable")
        result = self.content(
            "get_concept_neighborhood", code="C1", kinds=["inverseRole"], includeNegative=True
        )
        edge = result["edges"][0]
        self.assertEqual((edge["sourceCode"], edge["targetCode"]), ("C2", "C1"))
        self.assertEqual(edge["provenance"]["polarity"], "negative")

    def test_negative_target_at_the_depth_bound_needs_no_expansion(self):
        self.evs.concepts["C1"]["roles"] = [
            {"code": "R135", "type": "Exclusion", "relatedCode": "C2", "relatedName": "Two"}
        ]
        result = self.content("get_concept_neighborhood", code="C1", kinds=["role"], depth=1)
        edge = result["edges"][0]
        self.assertEqual((edge["sourceCode"], edge["targetCode"]), ("C1", "C2"))
        self.assertEqual(edge["provenance"]["polarity"], "negative")
        self.assertEqual({node["code"] for node in result["nodes"]}, {"C1", "C2"})

    def test_search_without_an_index_is_unavailable(self):
        result = self.content("search_concepts", query="One", mode="semantic")
        self.assertEqual(result["error"]["code"], "capability_unavailable")
        self.assertEqual(self.evs.calls, [])

    def test_hydration_request_exhaustion_keeps_the_earlier_node_bound(self):
        self.evs.concepts["C1"]["children"].append({"code": "C3", "name": "Three"})
        original = self.evs.get_concepts_by_codes

        def counted(*args, **kwargs):
            current_budget().request()
            return original(*args, **kwargs)

        with (
            patch("nci_si_mcp.content.Budget", return_value=Budget(depth=1, nodes=2, requests=1)),
            patch.object(self.evs, "get_concepts_by_codes", side_effect=counted),
        ):
            result = self.content("get_concept_neighborhood", code="C1", kinds=["child"])
        self.assertEqual(result["truncation"]["bound"], "nodes")
        self.assertEqual(result["truncation"]["limit"], 2)
        self.assertEqual([node["code"] for node in result["nodes"]], ["C1"])

    def test_an_unread_final_frontier_reports_requests_for_each_selected_kind(self):
        self.evs.concepts["C1"]["roles"] = [
            {"code": "R1", "relatedCode": "C3", "relatedName": "Three"}
        ]
        original = self.evs.get_concepts_by_codes

        def counted(*args, **kwargs):
            current_budget().request()
            return original(*args, **kwargs)

        kinds = ["child", "role", "association"]
        with (
            patch("nci_si_mcp.content.Budget", return_value=Budget(depth=1, requests=1)),
            patch.object(self.evs, "get_concepts_by_codes", side_effect=counted),
        ):
            result = self.content("get_concept_neighborhood", code="C1", kinds=kinds)
        cut = result["truncation"]
        self.assertEqual((cut["bound"], cut["omitted"]), ("requests", 1))
        for kind in kinds:
            self.assertEqual(cut["perKind"][kind]["bound"], "requests")
            self.assertEqual(cut["perKind"][kind]["omitted"], 0)
            self.assertFalse(cut["perKind"][kind]["exact"])

    def test_hydration_retains_prior_per_kind_cuts(self):
        self.evs.concepts["C1"]["children"].append({"code": "C3", "name": "Three"})
        self.evs.concepts["C1"]["roles"] = [{"code": "R1", "relatedCode": "C4"}]
        original = self.evs.get_concepts_by_codes

        def counted(*args, **kwargs):
            current_budget().request()
            return original(*args, **kwargs)

        with (
            patch("nci_si_mcp.content.Budget", return_value=Budget(depth=1, nodes=3, requests=1)),
            patch.object(self.evs, "get_concepts_by_codes", side_effect=counted),
        ):
            result = self.content(
                "get_concept_neighborhood", code="C1", kinds=["child", "role", "association"]
            )
        self.assertEqual(result["truncation"]["bound"], "nodes")
        self.assertEqual(result["truncation"]["perKind"]["child"]["bound"], "nodes")
        self.assertEqual(result["truncation"]["perKind"]["role"]["bound"], "requests")
        self.assertEqual(result["truncation"]["perKind"]["role"]["omitted"], 1)
        self.assertEqual(result["truncation"]["perKind"]["association"], {"occurred": False})

    def test_paging_and_paths_are_explicitly_unavailable(self):
        for arguments in ({"direction": "pathsToRoot"}, {"direction": "child", "cursor": "x"}):
            result = self.content("get_concept_hierarchy", code="C1", **arguments)
            self.assertEqual(result["error"]["code"], "capability_unavailable")

    def test_output_schemas_describe_success_and_failure(self):
        self.index()
        tools = {tool.name: tool for tool in self.session(lambda client: client.list_tools()).tools}
        for name, arguments in (
            ("get_concept", {"code": "C1"}),
            ("search_concepts", {"query": "One", "mode": "semantic"}),
            ("get_concept_hierarchy", {"code": "C1", "direction": "child"}),
            ("get_concept_neighborhood", {"code": "C1", "kinds": ["child"]}),
        ):
            with self.subTest(name=name):
                failed, result = self.call(name, terminology="ncit", release="26.06e", **arguments)
                self.assertFalse(failed)
                Draft202012Validator(tools[name].output_schema).validate(result)
                failed, error = self.call(name, terminology="ncit", release="../", **arguments)
                self.assertTrue(failed)
                Draft202012Validator(tools[name].output_schema).validate(error)

    def test_request_exhaustion_during_hydration_does_not_invent_node_status(self):
        original = self.evs.get_concepts_by_codes

        def counted(*args, **kwargs):
            current_budget().request()
            return original(*args, **kwargs)

        with (
            patch("nci_si_mcp.content.Budget", return_value=Budget(depth=1, requests=1)),
            patch.object(self.evs, "get_concepts_by_codes", side_effect=counted),
        ):
            result = self.content("get_concept_neighborhood", code="C1", kinds=["child"])
        self.assertEqual([n["code"] for n in result["nodes"]], ["C1"])
        self.assertEqual(result["edges"], [])
        self.assertEqual(
            result["truncation"],
            {
                "occurred": True,
                "bound": "requests",
                "limit": 1,
                "reached": 1,
                "omitted": 1,
                "exact": False,
            },
        )
