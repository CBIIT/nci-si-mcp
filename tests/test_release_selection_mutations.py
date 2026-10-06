"""Implicit selection must preserve validation, bounds and audit contracts."""

import json
import logging
from dataclasses import replace
from unittest.mock import patch

from fakes import concept, release, terminology_row
from nci_si_mcp.bounds import Budget
from nci_si_mcp.evs import EVSClient, EVSReleaseNotFoundError
from nci_si_mcp.registry import invoke
from nci_si_mcp.release_selection import SessionRelease, session_scope
from test_evs_client import FakeResponse
from test_server import ServerFixture


class ReleaseSelectionMutationTest(ServerFixture):
    def test_expired_search_cursor_discovers_the_configured_weekly_release(self):
        self.context.settings = replace(self.settings, release_channel="weekly")
        arguments = {
            "terminology": "ncit",
            "query": "Q",
            "mode": "lexical",
            "release": "26.06e",
            "limit": 1,
        }
        with patch.object(
            self.evs, "search_concepts", create=True, return_value=(2, [concept("C1", active=True)])
        ):
            first = invoke(self.context, "search_concepts", **arguments)
        self.evs.release = release("26.07d", channel="weekly")
        with patch.object(
            self.evs,
            "search_concepts",
            create=True,
            side_effect=EVSReleaseNotFoundError("withdrawn"),
        ):
            result = invoke(
                self.context, "search_concepts", **arguments, cursor=first["nextCursor"]
            )
        self.assertEqual(result["error"]["code"], "cursor_expired")
        self.assertEqual(
            result["error"]["details"], {"cursorRelease": "26.06e", "currentRelease": "26.07d"}
        )
        self.assertIn(("get_terminologies", "ncit", (True, "weekly")), self.evs.calls)

    def test_each_implicit_content_tool_validates_before_discovery(self):
        cases = (
            ("get_concept", {"code": "bad"}),
            ("get_concepts", {"codes": ["bad"]}),
            ("get_concept_subsets", {"code": "bad"}),
            ("get_concept_mappings", {"code": "bad"}),
            ("expand_value_set", {"code": "bad"}),
            ("search_concepts", {"query": "Q", "mode": "bad"}),
            ("get_concept_hierarchy", {"code": "bad", "direction": "child"}),
            ("get_concept_neighborhood", {"code": "C3262", "kinds": ["bad"]}),
            ("resolve_retired_code", {"code": "bad"}),
            ("list_relationships", {"terminology": "../bad"}),
        )
        for operation, arguments in cases:
            with self.subTest(operation=operation):
                self.evs.calls.clear()
                result = invoke(self.context, operation, **({"terminology": "ncit"} | arguments))
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(self.evs.calls, [])

    def test_hierarchy_clamps_page_limit_for_both_directions(self):
        for direction, relation in (("child", "children"), ("parent", "parents")):
            with self.subTest(direction=direction):
                children = [f"C{i}" for i in range(2, 1003)]
                self.evs.concepts = {code: concept(code, active=True) for code in children}
                self.evs.concepts["C1"] = concept(
                    "C1", active=True, **{relation: [{"code": code} for code in children]}
                )
                result = invoke(
                    self.context,
                    "get_concept_hierarchy",
                    terminology="ncit",
                    code="C1",
                    direction=direction,
                    limit=1001,
                )
                self.assertEqual(len(result["nodes"]), 1000)
                self.assertIn("nextCursor", result)

    def test_hierarchy_clamps_depth_for_both_directions(self):
        for direction, relation in (("child", "children"), ("parent", "parents")):
            with self.subTest(direction=direction):
                self.evs.concepts = {
                    f"C{i}": concept(f"C{i}", active=True, **{relation: [{"code": f"C{i + 1}"}]})
                    for i in range(1, 7)
                }
                result = invoke(
                    self.context,
                    "get_concept_hierarchy",
                    terminology="ncit",
                    code="C1",
                    direction=direction,
                    depth=5,
                )
                self.assertEqual(
                    {node["code"] for node in result["nodes"]}, {"C2", "C3", "C4", "C5"}
                )
                self.assertEqual(result["truncation"]["bound"], "depth")
                self.assertEqual(result["truncation"]["limit"], 4)

    def test_cursor_binds_the_applied_depth_for_both_directions(self):
        for direction, relation in (("child", "children"), ("parent", "parents")):
            with self.subTest(direction=direction):
                self.evs.concepts = {
                    "C1": concept(
                        "C1", active=True, **{relation: [{"code": "C2"}, {"code": "C3"}]}
                    ),
                    "C2": concept("C2", active=True),
                    "C3": concept("C3", active=True),
                }
                arguments = {
                    "terminology": "ncit",
                    "code": "C1",
                    "direction": direction,
                    "limit": 1,
                }
                first = invoke(self.context, "get_concept_hierarchy", **arguments, depth=5)
                continued = invoke(
                    self.context,
                    "get_concept_hierarchy",
                    **arguments,
                    depth=4,
                    cursor=first["nextCursor"],
                )
                self.assertNotIn("error", continued)
                self.assertEqual([node["code"] for node in continued["nodes"]], ["C3"])

    def test_discovery_spends_the_hierarchy_and_neighborhood_request_budget(self):
        self.context.evs = EVSClient("https://evs.invalid", max_attempts=1)
        for operation, arguments in (
            ("get_concept_hierarchy", {"direction": "child"}),
            ("get_concept_hierarchy", {"direction": "pathsToRoot"}),
            ("get_concept_neighborhood", {"kinds": ["child"]}),
        ):
            with (
                self.subTest(operation=operation, arguments=arguments),
                patch("nci_si_mcp.content.Budget", return_value=Budget(requests=1)),
                patch(
                    "nci_si_mcp.http_client._open",
                    side_effect=[
                        FakeResponse(json.dumps([terminology_row()]).encode()),
                        FakeResponse(b"[]"),
                    ],
                ) as opened,
            ):
                result = invoke(self.context, operation, terminology="ncit", code="C1", **arguments)
                self.assertEqual(result["error"]["code"], "bound_exceeded")
                self.assertEqual(
                    result["error"]["details"], {"bound": "requests", "limit": 1, "reached": 1}
                )
                self.assertEqual(opened.call_count, 1)

    def test_retired_lookup_audit_retains_implicit_selection(self):
        logging.disable(logging.NOTSET)
        with self.assertLogs("nci_si_mcp.audit", level="INFO") as captured:
            result = invoke(self.context, "resolve_retired_code", terminology="ncit", code="C3262")
        self.assertTrue(result["active"])
        self.assertEqual(
            captured.records[-1].structured["release"]["selection"], "freshly-resolved"
        )

    def test_failed_discovery_is_audited_but_a_nonselecting_tool_has_no_selection(self):
        logging.disable(logging.NOTSET)
        self.evs.rows = []
        with self.assertLogs("nci_si_mcp.audit", level="INFO") as captured:
            failed = invoke(self.context, "get_concept", terminology="ncit", code="C3262")
            listed = invoke(self.context, "list_terminologies")
        self.assertEqual(failed["error"]["code"], "release_not_available")
        self.assertNotIn("error", listed)
        self.assertEqual(
            captured.records[0].structured.get("release", {}).get("selection"), "freshly-resolved"
        )
        self.assertNotIn("selection", captured.records[1].structured.get("release", {}))

    def test_withdrawn_implicit_release_names_its_source(self):
        with session_scope(SessionRelease()):
            invoke(self.context, "get_concept", terminology="ncit", code="C3262")
            with patch.object(
                self.evs, "get_concept", side_effect=EVSReleaseNotFoundError("withdrawn")
            ):
                result = invoke(self.context, "get_concept", terminology="ncit", code="C3262")
        self.assertEqual(result["error"]["details"], {"requested": "26.06e", "source": "evs"})

    def test_explicit_release_retains_weekly_channel_and_accepts_long_identifiers(self):
        self.context.settings = replace(self.settings, release_channel="weekly")
        self.evs.release = release("2026.09-weekly", channel="weekly")
        self.evs.concepts = {"C1": concept("C1", active=True, version="2026.09-weekly")}
        logging.disable(logging.NOTSET)
        with self.assertLogs("nci_si_mcp.audit", level="INFO") as captured:
            result = invoke(
                self.context, "get_concept", terminology="ncit", code="C1", release="2026.09-weekly"
            )
        self.assertNotIn("error", result)
        self.assertEqual(result["provenance"]["release"]["identifier"], "2026.09-weekly")
        self.assertEqual(
            captured.records[-1].structured["release"]["selected"]["channel"], "weekly"
        )

    def test_expired_explicit_cursor_discovers_the_configured_weekly_release(self):
        self.context.settings = replace(self.settings, release_channel="weekly")
        self.evs.concepts = {
            "C1": concept("C1", active=True, children=[{"code": "C2"}, {"code": "C3"}]),
            "C2": concept("C2", active=True),
            "C3": concept("C3", active=True),
        }
        arguments = {
            "terminology": "ncit",
            "code": "C1",
            "direction": "child",
            "release": "26.06e",
            "limit": 1,
        }
        first = invoke(self.context, "get_concept_hierarchy", **arguments)
        self.evs.release = release("26.07d", channel="weekly")
        with patch.object(
            self.evs, "get_concepts_by_codes", side_effect=EVSReleaseNotFoundError("withdrawn")
        ):
            result = invoke(
                self.context, "get_concept_hierarchy", **arguments, cursor=first["nextCursor"]
            )
        self.assertEqual(result["error"]["code"], "cursor_expired")
        self.assertEqual(
            result["error"]["details"], {"cursorRelease": "26.06e", "currentRelease": "26.07d"}
        )
        self.assertIn(("get_terminologies", "ncit", (True, "weekly")), self.evs.calls)
