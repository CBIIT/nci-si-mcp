import base64
import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fakes import concept, release, terminology_row
from nci_si_mcp.evs import EVSClient, EVSReleaseNotFoundError
from nci_si_mcp.index_scoring import rank_page
from nci_si_mcp.registry import invoke
from test_evs_client import FakeResponse
from test_server import ServerFixture


class IndexedSearchTest(ServerFixture):
    def setUp(self):
        super().setUp()
        self.context.index.upsert_concepts(
            [concept(f"C{i}", "Kinase", active=True) for i in range(1, 6)],
            None,
            self.context.embedding_provider,
        )

    def search(self, **arguments):
        return invoke(
            self.context,
            "search_concepts",
            **(
                {
                    "terminology": "ncit",
                    "release": "26.06e",
                    "query": "Kinase",
                    "mode": "semantic",
                    "limit": 2,
                }
                | arguments
            ),
        )

    def test_both_indexed_modes_page_all_matches_with_stable_ties(self):
        for mode in ("semantic", "hybrid"):
            with self.subTest(mode=mode):
                first = self.search(mode=mode)
                second = self.search(mode=mode, cursor=first["nextCursor"])
                last = self.search(mode=mode, cursor=second["nextCursor"])
                codes = [
                    hit["concept"]["code"]
                    for page in (first, second, last)
                    for hit in page["results"]
                ]
                self.assertEqual(codes, [f"C{i}" for i in range(1, 6)])
                self.assertEqual([page["totalKnown"] for page in (first, second, last)], [5, 5, 5])
                self.assertNotIn("nextCursor", last)
                self.assertNotIn("truncation", first)

    def test_a_same_release_replacement_expires_the_cursor(self):
        first = self.search()
        self.context.index.upsert_concepts(
            [concept("C6", "Kinase", active=True)],
            None,
            self.context.embedding_provider,
        )
        result = self.search(cursor=first["nextCursor"])
        self.assertEqual(result["error"]["code"], "cursor_expired")
        self.assertEqual(
            result["error"]["details"],
            {
                "cursorRelease": "26.06e",
                "currentRelease": "26.06e",
            },
        )
        self.assertIn("build changed", result["error"]["message"])

    def test_retirement_filters_before_count_and_page_selection(self):
        self.evs.rows = [
            terminology_row()
            | {
                "metadata": {
                    "retiredStatusValue": "retired-test",
                    "conceptStatuses": ["retired-test"],
                }
            }
        ]
        self.context.index.upsert_concepts(
            [
                concept(f"C{i}", "Kinase", active=False, conceptStatus="retired-test")
                for i in (2, 4, 6)
            ],
            None,
            self.context.embedding_provider,
        )
        first = self.search(retired="only")
        last = self.search(retired="only", cursor=first["nextCursor"])
        self.assertEqual([hit["concept"]["code"] for hit in first["results"]], ["C2", "C4"])
        self.assertEqual([hit["concept"]["code"] for hit in last["results"]], ["C6"])
        self.assertEqual(first["totalKnown"], 3)
        self.assertNotIn("nextCursor", last)

    def test_missing_numpy_is_a_capability_error_not_a_partial_ranking(self):
        with patch.dict("sys.modules", {"numpy": None}):
            result = self.search()
        self.assertEqual(result["error"]["code"], "capability_unavailable")
        self.assertNotIn("results", result)

    def test_an_indexed_cursor_requires_its_internal_build_identity(self):
        first = self.search()
        payload = json.loads(base64.urlsafe_b64decode(first["nextCursor"]))
        for build in (None, "bad", 12):
            with self.subTest(build=build):
                token = base64.urlsafe_b64encode(
                    json.dumps(payload | {"build": build}).encode()
                ).decode()
                self.assertEqual(self.search(cursor=token)["error"]["code"], "invalid_request")

    def test_page_count_and_identity_share_a_snapshot_during_activation(self):
        replacement = self.context.index.build(
            [concept("C9", "Kinase", active=True)],
            None,
            self.context.embedding_provider,
        )

        def activate_then_rank(*args, **kwargs):
            self.context.index.activate(replacement.build_id)
            return rank_page(*args, **kwargs)

        with patch("nci_si_mcp.index.rank_page", side_effect=activate_then_rank):
            first = self.search()
        self.assertEqual(first["totalKnown"], 5)
        self.assertEqual([hit["concept"]["code"] for hit in first["results"]], ["C1", "C2"])
        continued = self.search(cursor=first["nextCursor"])
        self.assertEqual(continued["error"]["code"], "cursor_expired")


class LiveSearchTest(ServerFixture):
    def setUp(self):
        super().setUp()
        self.context.evs = EVSClient("https://evs.invalid", max_attempts=1)

    def search(self, **arguments):
        return invoke(
            self.context,
            "search_concepts",
            **({"terminology": "ncit", "release": "26.06e", "query": "kinase"} | arguments),
        )

    def replies(self, bodies):
        return patch(
            "nci_si_mcp.http_client._open",
            side_effect=[FakeResponse(json.dumps(body).encode()) for body in bodies],
        )

    def test_lexical_pages_preserve_order_highlights_and_stop_at_the_last_page(self):
        first = concept("C2", active=True, highlight="<em>kinase</em> & text")
        second = concept("C1", active=False, conceptStatus="Retired_Concept")
        with self.replies(
            [{"total": 2, "concepts": [first]}, {"total": 2, "concepts": [second]}]
        ) as opened:
            page = self.search(limit=1)
            last = self.search(limit=1, cursor=page["nextCursor"])
        self.assertEqual([p["results"][0]["concept"]["code"] for p in (page, last)], ["C2", "C1"])
        self.assertEqual(page["results"][0]["matchedOn"], first["highlight"])
        self.assertNotIn("score", page["results"][0])
        self.assertNotIn("matchedOn", last["results"][0])
        self.assertNotIn("nextCursor", last)
        self.assertNotIn("truncation", page)
        self.assertEqual(page["totalKnown"], 2)
        self.assertIn("fromRecord=1", opened.call_args_list[1].args[0].full_url)

    def test_typeahead_omits_score_and_highlight_and_encodes_the_whole_query(self):
        query = "a & b/+?é"
        row = concept("C1", active=True, highlight="unexpected")
        with self.replies([{"total": 1, "concepts": [row]}]) as opened:
            result = self.search(query=query, mode="typeahead")
        self.assertEqual(set(result["results"][0]), {"concept"})
        params = parse_qs(urlsplit(opened.call_args.args[0].full_url).query)
        self.assertEqual(params["term"], [query])
        self.assertEqual(params["type"], ["startsWith"])

    def test_empty_matches_have_live_provenance(self):
        with self.replies([{"total": 0}]):
            result = self.search()
        self.assertEqual(result["results"], [])
        self.assertEqual(result["totalKnown"], 0)
        self.assertEqual(result["provenance"]["release"]["identifier"], "26.06e")
        self.assertEqual(result["provenance"]["servedBy"], "live")

    def test_explicit_defaults_continue_a_cursor_issued_with_defaults_omitted(self):
        rows = [concept(f"C{i}", active=True) for i in range(1, 12)]
        with self.replies(
            [{"total": 11, "concepts": rows[:10]}, {"total": 11, "concepts": rows[10:]}]
        ):
            first = self.search()
            last = self.search(
                mode="lexical", retired="include", limit=10, cursor=first["nextCursor"]
            )
        self.assertEqual(last["results"][0]["concept"]["code"], "C11")
        self.assertNotIn("nextCursor", last)

    def test_changed_arguments_are_invalid_before_an_upstream_request(self):
        with self.replies([{"total": 2, "concepts": [concept("C1", active=True)]}]):
            first = self.search(limit=1)
        for change in (
            {"query": "other"},
            {"mode": "typeahead"},
            {"retired": "only"},
            {"limit": 2},
        ):
            with self.subTest(change=change), self.replies([]):
                result = self.search(**({"limit": 1, "cursor": first["nextCursor"]} | change))
                self.assertEqual(result["error"]["code"], "invalid_request")

    def test_deeply_nested_and_invalid_cursors_are_invalid_before_network(self):
        nested = base64.urlsafe_b64encode(("[" * 1500 + "0" + "]" * 1500).encode()).decode()
        for cursor in (nested, "", "bad", "e30=", "x" * 9000):
            with self.subTest(cursor=cursor[:12]), self.replies([]):
                self.assertEqual(self.search(cursor=cursor)["error"]["code"], "invalid_request")

    def test_retired_only_uses_the_status_named_by_the_pinned_listing(self):
        metadata = release().to_dict() | {
            "metadata": {"retiredStatusValue": "retired-test", "conceptStatuses": ["retired-test"]}
        }
        row = concept("C1", active=False, conceptStatus="retired-test")
        with self.replies([[metadata], {"total": 1, "concepts": [row]}]) as opened:
            result = self.search(retired="only")
        self.assertEqual(result["results"][0]["concept"]["status"], "retired-test")
        self.assertIn("conceptStatus=retired-test", opened.call_args_list[1].args[0].full_url)

    def test_unselectable_retirement_is_invalid_without_a_search_request(self):
        metadata = release().to_dict() | {
            "metadata": {"retiredStatusValue": "true", "conceptStatuses": ["DEFAULT"]}
        }
        with self.replies([[metadata]]):
            result = self.search(retired="only")
        self.assertEqual(result["error"]["code"], "invalid_request")

    def test_unusable_pages_and_wrong_release_fail_closed(self):
        row = concept("C1", active=True)
        pages = [
            {"total": True, "concepts": [row]},
            {"total": -1, "concepts": []},
            {"total": 1, "concepts": []},
            {"total": 0, "concepts": [row]},
            {"total": 1, "concepts": [None]},
            {"total": 1, "concepts": [row | {"version": "other"}]},
        ]
        for page in pages:
            with self.subTest(page=page), self.replies([page]):
                result = self.search()
                self.assertIn(result["error"]["code"], {"upstream_unavailable", "release_mismatch"})
                self.assertNotIn("results", result)

    def test_withdrawn_cursor_discovers_current_only_after_the_pinned_failure(self):
        with self.replies([{"total": 2, "concepts": [concept("C1", active=True)]}]):
            first = self.search(limit=1)
        with patch.object(
            self.context.evs, "search_concepts", side_effect=EVSReleaseNotFoundError("withdrawn")
        ):
            with self.replies([[terminology_row(version="new")]]):
                result = self.search(limit=1, cursor=first["nextCursor"])
            with self.replies([]):
                initial = self.search()
        self.assertEqual(result["error"]["code"], "cursor_expired")
        self.assertEqual(
            result["error"]["details"], {"cursorRelease": "26.06e", "currentRelease": "new"}
        )
        self.assertEqual(initial["error"]["code"], "release_not_available")

    def test_invalid_highlight_or_missing_code_cannot_be_a_successful_match(self):
        row = concept("C1", active=True)
        for change in ({"highlight": None}, {"code": ""}):
            with (
                self.subTest(change=change),
                self.replies([{"total": 1, "concepts": [row | change]}]),
            ):
                result = self.search()
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.assertNotIn("results", result)

    def test_retired_only_rejects_malformed_or_ambiguous_metadata(self):
        row = terminology_row()
        listings = [
            [row, row],
            [row | {"metadata": "bad"}],
            [row | {"metadata": {"conceptStatuses": "retired-test"}}],
        ]
        for listing in listings:
            with self.subTest(listing=listing), self.replies([listing]):
                self.assertEqual(
                    self.search(retired="only")["error"]["code"], "upstream_unavailable"
                )

    def test_retired_only_cannot_pass_through_an_active_match(self):
        metadata = terminology_row() | {
            "metadata": {
                "retiredStatusValue": "retired-test",
                "conceptStatuses": ["retired-test"],
            }
        }
        row = concept("C1", active=True, conceptStatus="retired-test")
        with self.replies([[metadata], {"total": 1, "concepts": [row]}]):
            result = self.search(retired="only")
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
